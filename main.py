import os
from typing import List, Optional

import uvicorn
from cacheout import Cache
from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, QueuePool
from sqlalchemy.orm import sessionmaker

from models import *

# App
App = FastAPI(docs_url=None, redoc_url=None)

# 数据库连接串
SQLALCHEMY_DATABASE_URL = f"sqlite:///{os.getenv('CONFIG_DIR', '.')}/server.db"
# 数据库引擎
Engine = create_engine(SQLALCHEMY_DATABASE_URL,
                       echo=False,
                       poolclass=QueuePool,
                       pool_pre_ping=True,
                       pool_size=1000,
                       pool_recycle=60 * 10,
                       max_overflow=0
                       )
# 数据库会话
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=Engine)
# 初始化数据库
Base.metadata.create_all(bind=Engine)

# 统计缓存
StatisticCache = Cache(maxsize=100, ttl=1800)


# 数据模型
class PluginStatistic(BaseModel):
    plugin_id: str


class PluginStatisticList(BaseModel):
    plugins: List[PluginStatistic]


class SubscribeStatistic(BaseModel):
    name: str
    year: Optional[str] = None
    type: str
    tmdbid: Optional[int] = None
    imdbid: Optional[str] = None
    tvdbid: Optional[int] = None
    doubanid: Optional[str] = None
    season: Optional[int] = None
    poster: Optional[str] = None
    backdrop: Optional[str] = None
    vote: Optional[float] = None
    description: Optional[str] = None


class SubscribeStatisticList(BaseModel):
    subscribes: List[SubscribeStatistic]


def get_db():
    """
    获取数据库会话
    :return: Session
    """
    db = None
    try:
        db = SessionLocal()
        yield db
    finally:
        if db:
            db.close()


@App.get("/")
async def root():
    return {
        "message": "MoviePilot Server is running ..."
    }


@App.get("/plugin/install/{pid}")
async def plugin_install(pid: str, db: Session = Depends(get_db)):
    """
    安装插件计数
    """
    # 查询数据库中是否存在
    plugin = PluginStatistics.read(db, pid)
    # 如果不存在则创建
    if not plugin:
        plugin = PluginStatistics(plugin_id=pid, count=1)
        plugin.create(db)
    # 如果存在则更新
    else:
        plugin.update(db, {"count": plugin.count + 1})

    return {
        "message": "success"
    }


@App.post("/plugin/install")
async def plugin_batch_install(plugins: PluginStatisticList, db: Session = Depends(get_db)):
    """
    安装插件计数
    """
    for plugin in plugins.plugins:
        await plugin_install(plugin.plugin_id, db)

    return {
        "message": "success"
    }


@App.get("/plugin/statistic")
async def plugin_statistic(db: Session = Depends(get_db)):
    """
    查询插件安装统计
    """
    if not StatisticCache.get('plugin'):
        statistics = PluginStatistics.list(db)
        StatisticCache.set('plugin', {
            sta.plugin_id: sta.count for sta in statistics
        })
    return StatisticCache.get('plugin')


@App.post("/subscribe/add")
async def subscribe_add(subscribe: SubscribeStatistic, db: Session = Depends(get_db)):
    """
    添加订阅统计
    """
    # 查询数据库中是否存在
    sub = SubscribeStatistics.read(db, mid=subscribe.tmdbid or subscribe.doubanid, season=subscribe.season)
    # 如果不存在则创建
    if not sub:
        sub = SubscribeStatistics(**subscribe.dict(), count=1)
        sub.create(db)
    # 如果存在则更新
    else:
        sub.update(db, {"count": sub.count + 1})

    return {
        "message": "success"
    }


@App.post("/subscribe/report")
async def subscribe_report(subscribes: SubscribeStatisticList, db: Session = Depends(get_db)):
    """
    批量添加订阅统计
    """
    for subscribe in subscribes.subscribes:
        await subscribe_add(subscribe, db)

    return {
        "message": "success"
    }


@App.get("/subscribe/statistic")
async def subscribe_statistic(stype: str, page: int = 1, count: int = 30,
                              db: Session = Depends(get_db)):
    """
    查询订阅统计
    """
    cache_key = f"subscribe_{stype}_{page}_{count}"
    if not StatisticCache.get(cache_key):
        statistics = SubscribeStatistics.list(db, stype=stype, page=page, count=count)
        StatisticCache.set(cache_key, [
            {
                "name": sta.name,
                "year": sta.year,
                "type": sta.type,
                "tmdbid": sta.tmdbid,
                "imdbid": sta.imdbid,
                "tvdbid": sta.tvdbid,
                "doubanid": sta.doubanid,
                "bangumiid": sta.bangumiid,
                "season": sta.season,
                "poster": sta.poster,
                "backdrop": sta.backdrop,
                "vote": sta.vote,
                "description": sta.description,
                "count": sta.count
            } for sta in statistics
        ])
    return StatisticCache.get(cache_key)


if __name__ == '__main__':
    uvicorn.run('main:App', host="0.0.0.0", port=3001, reload=False)
