"""
运行期数据库调试与错误文本不能记录绑定的知识正文或凭据。
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.database import create_engine


def test_debug_and_sql_errors_hide_bound_values(monkeypatch, tmp_path, caplog, capsys):
    """
    使用真实引擎的调试日志和约束错误验证参数隐藏，而不只断言配置值。
    """
    secret = "synthetic-knowledge-private-body-not-for-logs"
    monkeypatch.setattr(settings, "DATABASE_TYPE", "sqlite")
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "DEBUG", True)

    async def scenario():
        """
        执行实际参数化写入及重复约束异常后关闭引擎。
        """
        engine = create_engine()
        try:
            async with engine.begin() as db:
                await db.execute(text("CREATE TABLE log_probe (content TEXT UNIQUE)"))
                await db.execute(text("INSERT INTO log_probe VALUES (:content)"), {"content": secret})
                with pytest.raises(IntegrityError) as error:
                    await db.execute(text("INSERT INTO log_probe VALUES (:content)"), {"content": secret})
                assert secret not in str(error.value)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
    captured = capsys.readouterr()
    assert secret not in caplog.text + captured.out + captured.err
    assert "SQL parameters hidden" in caplog.text + captured.out
