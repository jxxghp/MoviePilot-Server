"""
在独立 JavaScript 上下文执行真实通知网页和 worker，覆盖详情展示与 App 跳转边界。
"""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="通知网页回归需要 Node.js，正式 CI 已提供")
def test_push_page_and_service_worker():
    """
    使用 Node 内置测试器，无外部浏览器、推送服务、账号或新增依赖。
    """
    subprocess.run([shutil.which("node"), "--test", str(Path(__file__).with_suffix(".cjs"))], check=True)
