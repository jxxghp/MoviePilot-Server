"""
向系统推送服务投递通知，不维护用户和设备归属。
"""

import asyncio
import base64
import json
import time
from pathlib import Path

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush
from requests import RequestException
from redis.exceptions import RedisError

from app.core.config import Settings, settings
from app.models.push import PushDevice
from app.schemas.push import PushMessage
from app.services.push.messages import save_message


class PushDelivery:
    """
    每个 worker 复用一个 HTTP/2 客户端，退出时释放；私钥始终留在 Server。
    """

    def __init__(self, config: Settings = settings, transport=None):
        """
        保留配置与可注入传输，测试不连接 Apple 或浏览器推送服务。
        """
        self.config = config
        self.transport = transport
        self.client = None
        self.jwt_cache = ("", 0.0)
        self.web_semaphore = asyncio.Semaphore(4)

    async def close(self) -> None:
        """
        等待已有请求完成后关闭该 worker 的 HTTP 连接池。
        """
        if self.client:
            await self.client.aclose()
            self.client = None

    def apns_available(self) -> bool:
        """
        判断必要的 APNs 服务配置是否齐全，不代表签名或实际投递已经验证。
        """
        return all((self.config.PUSH_APNS_KEY_FILE, self.config.PUSH_APNS_KEY_ID,
                    self.config.PUSH_APNS_TEAM_ID, self.config.PUSH_APNS_TOPICS))

    def public_key(self) -> str:
        """
        从持久化 P-256 私钥导出浏览器所需的公开 VAPID 密钥。
        """
        if not self.config.PUSH_VAPID_KEY_FILE or not self.config.PUSH_VAPID_SUBJECT:
            return ""
        private = serialization.load_pem_private_key(Path(self.config.PUSH_VAPID_KEY_FILE).read_bytes(), password=None)
        if not isinstance(private, ec.EllipticCurvePrivateKey) or not isinstance(private.curve, ec.SECP256R1):
            raise ValueError("VAPID 必须使用 P-256 私钥")
        public = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(public).decode().rstrip("=")

    def _apns_jwt(self) -> str:
        """
        每五十分钟更新 ES256 provider token，设备令牌不参与签名。
        """
        token, expires = self.jwt_cache
        if time.time() < expires:
            return token
        token = jwt.encode({"iss": self.config.PUSH_APNS_TEAM_ID, "iat": int(time.time())},
                           Path(self.config.PUSH_APNS_KEY_FILE).read_bytes(), algorithm="ES256",
                           headers={"kid": self.config.PUSH_APNS_KEY_ID})
        self.jwt_cache = (token, time.time() + 3000)
        return token

    async def send(self, device: PushDevice, message: PushMessage) -> tuple[bool, bool, str]:
        """
        返回已接受、设备失效和受控原因；失败不向调用方暴露端点、令牌或私钥。
        """
        if device.transport == "webpush":
            async with self.web_semaphore:
                try:
                    identifier = await save_message(device, message)
                except RedisError:
                    return False, False, "消息全文暂存失败，请稍后重试"
                return await asyncio.to_thread(self._send_web, device, message, identifier)
        return await self._send_apns(device, message)

    async def _send_apns(self, device: PushDevice, message: PushMessage) -> tuple[bool, bool, str]:
        """
        使用设备注册时的 topic 和环境发送，4 KiB 上限按实际 UTF-8 字节检查。
        """
        if not self.apns_available() or device.topic not in self.config.PUSH_APNS_TOPICS.split(","):
            return False, False, "APNs 未配置"
        payload = {"aps": {"alert": {"title": message.title, "body": message.body}, "sound": "default"},
                   "moviepilot": {"origin": device.origin, "route": message.route, "owner": device.owner}}
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) > 4096:
            return False, False, "通知超过 APNs 4 KiB 上限"
        host = "api.sandbox.push.apple.com" if device.environment == "sandbox" else "api.push.apple.com"
        try:
            authorization = await asyncio.to_thread(self._apns_jwt)
            if self.client is None:
                self.client = httpx.AsyncClient(http2=True, timeout=15, transport=self.transport)
            response = await self.client.post(f"https://{host}/3/device/{device.destination}", content=encoded,
                                              headers={"authorization": f"bearer {authorization}", "apns-topic": device.topic,
                                                       "apns-push-type": "alert", "apns-priority": "10", "apns-expiration": "0"})
            if response.status_code == 200:
                return True, False, "已提交 APNs"
            try:
                reason = response.json().get("reason", "")
            except (ValueError, AttributeError):
                reason = ""
            gone = response.status_code == 410 and reason == "Unregistered"
            return False, gone, f"APNs 拒绝（{response.status_code}）"
        except (httpx.HTTPError, OSError, ValueError, jwt.PyJWTError):
            return False, False, "APNs 连接或密钥配置失败"

    def _send_web(self, device: PushDevice, message: PushMessage, identifier: str = "") -> tuple[bool, bool, str]:
        """
        浏览器订阅由 Server 的 VAPID 身份加密，404/410 才清理失效端点。
        """
        if not self.config.PUSH_VAPID_KEY_FILE or not self.config.PUSH_VAPID_SUBJECT:
            return False, False, "Web Push 未配置"
        payload = {"title": message.title, "body": message.body,
                   "url": device.origin + "/#" + message.route, "origin": device.origin,
                   "route": message.route, "owner": device.owner}
        if message.image:
            payload["image"] = message.image
        if identifier:
            payload["id"] = identifier
            payload["body"] = message.body.encode()[:1024].decode("utf-8", errors="ignore")
            # 长正文与极长图片/实例地址保留在全文中，系统预览不能突破加密载荷上限。
            for field in ("url", "body", "image", "route", "origin"):
                if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()) <= 3800:
                    break
                payload.pop(field, None)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode()) > 3800:
            return False, False, "通知超过 Web Push 大小上限"
        try:
            webpush(subscription_info=json.loads(device.destination), data=encoded,
                    vapid_private_key=self.config.PUSH_VAPID_KEY_FILE,
                    vapid_claims={"sub": self.config.PUSH_VAPID_SUBJECT}, ttl=300, timeout=10)
            return True, False, "已提交 Web Push"
        except WebPushException as error:
            status = getattr(error.response, "status_code", 0)
            return False, status in {404, 410}, f"Web Push 拒绝（{status}）"
        except (OSError, ValueError, RequestException):
            return False, False, "Web Push 密钥或订阅无效"


push_delivery = PushDelivery()
