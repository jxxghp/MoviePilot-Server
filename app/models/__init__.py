"""
数据库模型导入
"""
from app.models.base import Base, get_id_column
from app.models import knowledge as _knowledge  # 注册知识表，供统一结构初始化创建。
from app.models.mapping import MediaMapping
from app.models.admin import AdminAudit, AdminSetting, AdminUser
from app.models.plugin_rating import PluginRating, PluginRatingSummary
from app.models.plugin_statistic import PluginStatistics
from app.models.push import PushDevice, PushRelay, PushRelayKey
from app.models.subscribe_share import SubscribeShare
from app.models.subscribe_statistics import SubscribeStatistics
from app.models.usage_statistic import IOSUsageStatistics, UsageStatistics
from app.models.workflow_share import WorkflowShare

__all__ = [
    "Base",
    "MediaMapping",
    "PushDevice",
    "PushRelay",
    "PushRelayKey",
    "AdminAudit",
    "AdminSetting",
    "AdminUser",
    "get_id_column",
    "PluginRating",
    "PluginRatingSummary",
    "PluginStatistics",
    "SubscribeStatistics",
    "SubscribeShare",
    "UsageStatistics",
    "IOSUsageStatistics",
    "WorkflowShare"
]
