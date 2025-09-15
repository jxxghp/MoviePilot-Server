"""
数据库基础模型
"""
from sqlalchemy import Column, Integer, Identity, Sequence
from sqlalchemy.orm import declarative_base

from app.core.config import settings

Base = declarative_base()


def get_id_column():
    """
    根据数据库类型返回合适的ID列定义
    """
    if settings.is_postgresql:
        # PostgreSQL使用SERIAL类型，让数据库自动处理序列
        return Column(Integer, Identity(start=1, cycle=True), primary_key=True, index=True)
    else:
        # SQLite使用Sequence
        return Column(Integer, Sequence('id'), primary_key=True, index=True)
