import os
from datetime import datetime
from typing import List, Optional

import uvicorn
from cacheout import Cache
from fastapi import FastAPI, Depends
from pydantic import BaseModel  # noqa
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from models import *

# 数据库连接串
SQLALCHEMY_DATABASE_URL = f"sqlite+aiosqlite:///{os.getenv('CONFIG_DIR', '.')}/server.db"
# 数据库引擎
Engine = create_async_engine(SQLALCHEMY_DATABASE_URL,
                             echo=False,
                             pool_pre_ping=True,
                             pool_size=20,
                             max_overflow=10,
                             pool_recycle=3600,
                             pool_timeout=180,
                             )
# 数据库会话
AsyncSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=Engine)

# 统计缓存
StatisticCache = Cache(maxsize=100, ttl=1800)

# 订阅分享缓存
ShareCache = Cache(maxsize=100, ttl=1800)

# 工作流分享缓存
WorkflowShareCache = Cache(maxsize=100, ttl=1800)


# 数据模型
class PluginStatisticItem(BaseModel):
    plugin_id: str


class PluginStatisticList(BaseModel):
    plugins: List[PluginStatisticItem]


class SubscribeStatisticItem(BaseModel):
    name: Optional[str] = None
    year: Optional[str] = None
    type: Optional[str] = None
    tmdbid: Optional[int] = None
    imdbid: Optional[str] = None
    tvdbid: Optional[int] = None
    doubanid: Optional[str] = None
    season: Optional[int] = None
    poster: Optional[str] = None
    backdrop: Optional[str] = None
    vote: Optional[float] = None
    description: Optional[str] = None


class SubscribeShareItem(BaseModel):
    id: Optional[int] = None
    share_title: Optional[str] = None
    share_comment: Optional[str] = None
    share_user: Optional[str] = None
    share_uid: Optional[str] = None
    name: Optional[str] = None
    year: Optional[str] = None
    type: Optional[str] = None
    keyword: Optional[str] = None
    tmdbid: Optional[int] = None
    imdbid: Optional[str] = None
    tvdbid: Optional[int] = None
    doubanid: Optional[str] = None
    season: Optional[int] = None
    poster: Optional[str] = None
    backdrop: Optional[str] = None
    vote: Optional[float] = None
    description: Optional[str] = None
    genre_ids: Optional[str] = None
    include: Optional[str] = None
    exclude: Optional[str] = None
    quality: Optional[str] = None
    resolution: Optional[str] = None
    effect: Optional[str] = None
    total_episode: Optional[int] = None
    custom_words: Optional[str] = None
    media_category: Optional[str] = None
    episode_group: Optional[str] = None
    date: Optional[str] = None


class WorkflowShareItem(BaseModel):
    id: Optional[int] = None
    share_title: Optional[str] = None
    share_comment: Optional[str] = None
    share_user: Optional[str] = None
    share_uid: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    timer: Optional[str] = None
    actions: Optional[str] = None  # JSON字符串
    flows: Optional[str] = None  # JSON字符串
    context: Optional[str] = None  # JSON字符串
    date: Optional[str] = None


class SubscribeStatisticList(BaseModel):
    subscribes: List[SubscribeStatisticItem]


class SubscribeShareStatisticItem(BaseModel):
    share_user: str
    share_count: int
    total_reuse_count: int


async def get_db():
    """
    获取数据库会话
    :return: AsyncSession
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_: FastAPI):
    """
    应用生命周期管理
    """
    # 启动时初始化数据库
    async with Engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # 关闭时清理资源
    await Engine.dispose()


# FastAPI 应用实例
App = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)


@App.get("/")
async def root():
    return {
        "code": 0,
        "message": "MoviePilot Server is running ..."
    }


@App.get("/plugin/install/{pid}")
async def plugin_install(pid: str, db: AsyncSession = Depends(get_db)):
    """
    安装插件计数
    """
    # 查询数据库中是否存在
    plugin = await PluginStatistics.read(db, pid)
    # 如果不存在则创建
    if not plugin:
        plugin = PluginStatistics(plugin_id=pid, count=1)
        await plugin.create(db)
    # 如果存在则更新
    else:
        await plugin.update(db, {"count": plugin.count + 1})

    return {
        "code": 0,
        "message": "success"
    }


@App.post("/plugin/install")
async def plugin_batch_install(plugins: PluginStatisticList, db: AsyncSession = Depends(get_db)):
    """
    安装插件计数
    """
    for plugin in plugins.plugins:
        await plugin_install(plugin.plugin_id, db)

    return {
        "code": 0,
        "message": "success"
    }


@App.get("/plugin/statistic")
async def plugin_statistic(db: AsyncSession = Depends(get_db)):
    """
    查询插件安装统计
    """
    if not StatisticCache.get('plugin'):
        statistics = await PluginStatistics.list(db)
        StatisticCache.set('plugin', {
            sta.plugin_id: sta.count for sta in statistics
        })
    return StatisticCache.get('plugin')


@App.post("/subscribe/add")
async def subscribe_add(subscribe: SubscribeStatisticItem, db: AsyncSession = Depends(get_db)):
    """
    添加订阅统计
    """
    # 查询数据库中是否存在
    sub = await SubscribeStatistics.read(db, mid=subscribe.tmdbid or subscribe.doubanid, season=subscribe.season)
    # 如果不存在则创建
    if not sub:
        sub = SubscribeStatistics(**subscribe.dict(), count=1)  # noqa
        await sub.create(db)
    # 如果存在则更新
    else:
        await sub.update(db, {"count": sub.count + 1})

    return {
        "code": 0,
        "message": "success"
    }


@App.post("/subscribe/done")
async def subscribe_done(subscribe: SubscribeStatisticItem, db: AsyncSession = Depends(get_db)):
    """
    完成订阅更新统计
    """
    # 查询数据库中是否存在
    sub = await SubscribeStatistics.read(db, mid=subscribe.tmdbid or subscribe.doubanid, season=subscribe.season)
    # 如果存在则更新
    if sub:
        if sub.count <= 1:
            await sub.delete(db, sub.id)
        else:
            await sub.update(db, {"count": sub.count - 1})

    return {
        "code": 0,
        "message": "success"
    }


@App.post("/subscribe/report")
async def subscribe_report(subscribes: SubscribeStatisticList, db: AsyncSession = Depends(get_db)):
    """
    批量添加订阅统计
    """
    for subscribe in subscribes.subscribes:
        await subscribe_add(subscribe, db)

    return {
        "code": 0,
        "message": "success"
    }


@App.get("/subscribe/statistic")
async def subscribe_statistic(stype: str, page: int = 1, count: int = 30,
                              db: AsyncSession = Depends(get_db)):
    """
    查询订阅统计
    """
    cache_key = f"subscribe_{stype}_{page}_{count}"
    if not StatisticCache.get(cache_key):
        statistics = await SubscribeStatistics.list(db, stype=stype, page=page, count=count)
        StatisticCache.set(cache_key, [
            sta.dict() for sta in statistics
        ])
    return StatisticCache.get(cache_key)


@App.post("/subscribe/share")
async def subscribe_share(subscribe: SubscribeShareItem, db: AsyncSession = Depends(get_db)):
    """
    新增订阅分享
    """
    if not subscribe.share_title or not subscribe.share_user:
        return {
            "code": 1,
            "message": "请填写分享标题和说明"
        }
    # 查询数据库中是否存在
    sub = await SubscribeShare.read(db, title=subscribe.share_title, user=subscribe.share_user)
    # 如果不存在则创建
    if not sub:
        subscribe.date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sub = SubscribeShare(**subscribe.dict(), count=1)  # noqa
        await sub.create(db)
    # 如果存在则报错
    else:
        return {
            "code": 2,
            "message": "您使用的昵称已经分享过这个订阅了"
        }

    # 清除缓存
    ShareCache.clear()

    return {
        "code": 0,
        "message": "success"
    }


@App.get("/subscribe/share/statistics")
async def subscribe_share_statistics(db: AsyncSession = Depends(get_db)):
    """
    查询订阅分享统计
    返回每个分享人分享的媒体数量以及总的复用人次
    """
    cache_key = "subscribe_share_statistics"
    if not ShareCache.get(cache_key):
        statistics = await SubscribeShare.share_statistics(db)
        ShareCache.set(cache_key, [
            {
                "share_user": stat.share_user,
                "share_count": stat.share_count,
                "total_reuse_count": stat.total_reuse_count or 0
            } for stat in statistics
        ])
    return ShareCache.get(cache_key)


@App.delete("/subscribe/share/{sid}")
async def subscribe_share_delete(sid: int, share_uid: str, db: AsyncSession = Depends(get_db)):
    """
    删除订阅分享
    """
    # 查询数据库中是否存在
    sub = await SubscribeShare.read_by_id(db, sid)

    # 如果存在则删除
    if sub and share_uid:
        await sub.delete(db, sid)
        # 清除缓存
        ShareCache.clear()
        return {
            "code": 0,
            "message": "success"
        }
    return {
        "code": 1,
        "message": "分享不存在或无权限"
    }


@App.get("/subscribe/shares")
async def subscribe_shares(name: str = None, page: int = 1, count: int = 30,
                           db: AsyncSession = Depends(get_db)):
    """
    查询分享的订阅
    """
    # 查询数据库中是否存在
    cache_key = f"subscribe_{name}_{page}_{count}"
    if not ShareCache.get(cache_key):
        shares = await SubscribeShare.list(db, name=name, page=page, count=count)
        ShareCache.set(cache_key, [
            sha.dict() for sha in shares
        ])
    return ShareCache.get(cache_key)


@App.get("/subscribe/fork/{shareid}")
async def subscribe_fork(shareid: int, db: AsyncSession = Depends(get_db)):
    """
    复用分享的订阅
    """
    # 查询数据库中是否存在
    share = await SubscribeShare.read_by_id(db, sid=shareid)
    # 如果存在则更新
    if share:
        await share.update(db, {"count": share.count + 1})

    return {
        "code": 0,
        "message": "success"
    }


# 工作流分享相关接口
@App.post("/workflow/share")
async def workflow_share(workflow: WorkflowShareItem, db: AsyncSession = Depends(get_db)):
    """
    新增工作流分享
    """
    if not workflow.share_title or not workflow.share_user:
        return {
            "code": 1,
            "message": "请填写分享标题和分享人"
        }
    # 查询数据库中是否存在
    share = await WorkflowShare.read(db, title=workflow.share_title, user=workflow.share_user)
    # 如果不存在则创建
    if not share:
        workflow.date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        share = WorkflowShare(**workflow.dict(), count=0)  # noqa
        await share.create(db)
    # 如果存在则报错
    else:
        return {
            "code": 2,
            "message": "您使用的昵称已经分享过这个工作流了"
        }

    # 清除缓存
    WorkflowShareCache.clear()

    return {
        "code": 0,
        "message": "success"
    }


@App.delete("/workflow/share/{sid}")
async def workflow_share_delete(sid: int, share_uid: str, db: AsyncSession = Depends(get_db)):
    """
    删除工作流分享
    """
    # 查询数据库中是否存在
    share = await WorkflowShare.read_by_id(db, sid)

    # 如果存在则删除
    if share and share_uid:
        await share.delete(db, sid)
        # 清除缓存
        WorkflowShareCache.clear()
        return {
            "code": 0,
            "message": "success"
        }
    return {
        "code": 1,
        "message": "分享不存在或无权限"
    }


@App.get("/workflow/shares")
async def workflow_shares(name: str = None, page: int = 1, count: int = 30,
                          db: AsyncSession = Depends(get_db)):
    """
    查询分享的工作流
    """
    # 查询数据库中是否存在
    cache_key = f"workflow_{name}_{page}_{count}"
    if not WorkflowShareCache.get(cache_key):
        shares = await WorkflowShare.list(db, name=name, page=page, count=count)
        WorkflowShareCache.set(cache_key, [
            sha.dict() for sha in shares
        ])
    return WorkflowShareCache.get(cache_key)


@App.get("/workflow/fork/{shareid}")
async def workflow_fork(shareid: int, db: AsyncSession = Depends(get_db)):
    """
    复用分享的工作流
    """
    # 查询数据库中是否存在
    share = await WorkflowShare.read_by_id(db, sid=shareid)
    # 如果存在则更新
    if share:
        await share.update(db, {"count": share.count + 1})

    return {
        "code": 0,
        "message": "success"
    }


if __name__ == '__main__':
    uvicorn.run('main:App', host="0.0.0.0", port=3001, reload=False)
