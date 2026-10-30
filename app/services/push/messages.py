"""
短期保存网页通知全文，按设备绑定版本隔离，不建立中心永久消息历史。
"""

import json
import secrets

from app.db.redis import get_redis
from app.models.push import PushDevice
from app.schemas.push import PushMessage
from app.services.push.registry import digest

MESSAGE_TTL = 86400
MESSAGE_LIMIT = 20
STORE = """
redis.call('SET', KEYS[1] .. ':' .. ARGV[1], ARGV[2], 'EX', ARGV[3])
redis.call('LPUSH', KEYS[1], ARGV[1])
local removed = redis.call('LRANGE', KEYS[1], ARGV[4], -1)
redis.call('LTRIM', KEYS[1], 0, tonumber(ARGV[4]) - 1)
redis.call('EXPIRE', KEYS[1], ARGV[3])
for _, id in ipairs(removed) do redis.call('DEL', KEYS[1] .. ':' .. id) end
return 1
"""


def message_key(device: PushDevice) -> str:
    """
    绑定版本参与缓存空间，换绑后不能读取前一用户或前一设备的消息。
    """
    return "push:messages:" + digest(f"{device.namespace}:{device.id}:{device.revision}")


async def save_message(device: PushDevice, message: PushMessage) -> str:
    """
    投递前原子暂存全文，每个绑定最多二十条，每条独立在二十四小时后过期。
    """
    identifier = secrets.token_hex(16)
    payload = {**message.model_dump(exclude_none=True), "id": identifier, "origin": device.origin}
    await get_redis().eval(STORE, 1, message_key(device), identifier,
                           json.dumps(payload, ensure_ascii=False), MESSAGE_TTL, MESSAGE_LIMIT)
    return identifier


async def read_message(device: PushDevice, identifier: str) -> dict:
    """
    调用方先验证当前绑定和实例状态，只返回该绑定版本仍保留的全文。
    """
    raw = await get_redis().get(message_key(device) + ":" + identifier)
    return json.loads(raw) if raw else {}


async def latest_message(device: PushDevice) -> dict:
    """
    旧 worker 未保存点击信息时，只读取本绑定最新一条仍有效的全文。
    """
    identifiers = await get_redis().lrange(message_key(device), 0, 0)
    return await read_message(device, identifiers[0]) if identifiers else {}
