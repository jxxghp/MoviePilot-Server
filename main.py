import os
from datetime import datetime
from typing import List, Optional

import uvicorn
from cacheout import Cache
from fastapi import FastAPI, Depends
from pydantic import BaseModel # noqa
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
                       pool_size=1024,
                       pool_recycle=3600,
                       pool_timeout=180,
                       max_overflow=10,
                       connect_args={"timeout": 60}
                       )
# 数据库会话
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=Engine)
# 初始化数据库
Base.metadata.create_all(bind=Engine)

# 统计缓存
StatisticCache = Cache(maxsize=100, ttl=1800)

# 订阅分享缓存
ShareCache = Cache(maxsize=100, ttl=1800)


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
    genre_ids: Optional[str] = None
    include: Optional[str] = None
    exclude: Optional[str] = None
    quality: Optional[str] = None
    resolution: Optional[str] = None
    effect: Optional[str] = None
    total_episode: Optional[int] = None
    custom_words: Optional[str] = None
    media_category: Optional[str] = None
    date: Optional[str] = None


class SubscribeStatisticList(BaseModel):
    subscribes: List[SubscribeStatisticItem]


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
def root():
    return {
        "code": 0,
        "message": "MoviePilot Server is running ..."
    }


@App.get("/plugin/install/{pid}")
def plugin_install(pid: str, db: Session = Depends(get_db)):
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
        "code": 0,
        "message": "success"
    }


@App.post("/plugin/install")
def plugin_batch_install(plugins: PluginStatisticList, db: Session = Depends(get_db)):
    """
    安装插件计数
    """
    for plugin in plugins.plugins:
        plugin_install(plugin.plugin_id, db)

    return {
        "code": 0,
        "message": "success"
    }


@App.get("/plugin/statistic")
def plugin_statistic(db: Session = Depends(get_db)):
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
def subscribe_add(subscribe: SubscribeStatisticItem, db: Session = Depends(get_db)):
    """
    添加订阅统计
    """
    # 查询数据库中是否存在
    sub = SubscribeStatistics.read(db, mid=subscribe.tmdbid or subscribe.doubanid, season=subscribe.season)
    # 如果不存在则创建
    if not sub:
        sub = SubscribeStatistics(**subscribe.dict(), count=1) # noqa
        sub.create(db)
    # 如果存在则更新
    else:
        sub.update(db, {"count": sub.count + 1})

    return {
        "code": 0,
        "message": "success"
    }


@App.post("/subscribe/done")
def subscribe_done(subscribe: SubscribeStatisticItem, db: Session = Depends(get_db)):
    """
    完成订阅更新统计
    """
    # 查询数据库中是否存在
    sub = SubscribeStatistics.read(db, mid=subscribe.tmdbid or subscribe.doubanid, season=subscribe.season)
    # 如果存在则更新
    if sub:
        if sub.count <= 1:
            sub.delete(db, sub.id)
        else:
            sub.update(db, {"count": sub.count - 1})

    return {
        "code": 0,
        "message": "success"
    }


@App.post("/subscribe/report")
def subscribe_report(subscribes: SubscribeStatisticList, db: Session = Depends(get_db)):
    """
    批量添加订阅统计
    """
    for subscribe in subscribes.subscribes:
        subscribe_add(subscribe, db)

    return {
        "code": 0,
        "message": "success"
    }


@App.get("/subscribe/statistic")
def subscribe_statistic(stype: str, page: int = 1, count: int = 30,
                        db: Session = Depends(get_db)):
    """
    查询订阅统计
    """
    cache_key = f"subscribe_{stype}_{page}_{count}"
    if not StatisticCache.get(cache_key):
        statistics = SubscribeStatistics.list(db, stype=stype, page=page, count=count)
        StatisticCache.set(cache_key, [
            sta.dict() for sta in statistics
        ])
    return StatisticCache.get(cache_key)


@App.post("/subscribe/share")
def subscribe_share(subscribe: SubscribeShareItem, db: Session = Depends(get_db)):
    """
    新增订阅分享
    """
    if not subscribe.share_title or not subscribe.share_user:
        return {
            "code": 1,
            "message": "请填写分享标题和说明"
        }
    # 查询数据库中是否存在
    sub = SubscribeShare.read(db, title=subscribe.share_title, user=subscribe.share_user)
    # 如果不存在则创建
    if not sub:
        subscribe.date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sub = SubscribeShare(**subscribe.dict(), count=1) # noqa
        sub.create(db)
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


@App.delete("/subscribe/share/{sid}")
def subscribe_share_delete(sid: int, share_uid: str, db: Session = Depends(get_db)):
    """
    删除订阅分享
    """
    # 查询数据库中是否存在
    sub = SubscribeShare.read_by_id(db, sid)

    # 如果存在则删除
    if sub and sub.share_uid == share_uid:
        sub.delete(db, sid)
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
def subscribe_shares(name: str = None, page: int = 1, count: int = 30,
                     db: Session = Depends(get_db)):
    """
    查询分享的订阅
    """
    # 查询数据库中是否存在
    cache_key = f"subscribe_{name}_{page}_{count}"
    if not ShareCache.get(cache_key):
        shares = SubscribeShare.list(db, name=name, page=page, count=count)
        ShareCache.set(cache_key, [
            sha.dict() for sha in shares
        ])
    return ShareCache.get(cache_key)


@App.get("/subscribe/fork/{shareid}")
def subscribe_fork(shareid: int, db: Session = Depends(get_db)):
    """
    复用分享的订阅
    """
    # 查询数据库中是否存在
    share = SubscribeShare.read_by_id(db, sid=shareid)
    # 如果存在则更新
    if share:
        share.update(db, {"count": share.count + 1})

    return {
        "code": 0,
        "message": "success"
    }


if __name__ == '__main__':
    uvicorn.run('main:App', host="0.0.0.0", port=3001, reload=False)
