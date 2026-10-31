"""多识别源统计、分享和数据库升级测试。"""

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.cache import cache_manager
from app.core.media import (
    LEGACY_MEDIA_SOURCE_ID_FIELDS,
    MediaSource,
    build_legacy_media_identity,
)
from app.db.deps import get_db
from app.models import Base, SubscribeShare, SubscribeStatistics
from app.schemas.models import (
    MediaRecognizeShareItem,
    SubscribeShareItem,
    SubscribeStatisticItem,
)
from app.services.database_schema import ensure_database_schema
from app.services.media_recognize_share import MediaRecognizeShareService
from app.services.subscribe_share import SubscribeShareService
from app.services.subscribe_statistic import SubscribeService
from main import App


def test_schemas_normalize_builtin_and_plugin_identities() -> None:
    """中心服务保存模型应同时接受内置与插件扩展的统一身份。"""
    identity_payload = {
        "media_source": MediaSource.AniList,
        "media_id": "subject-126",
    }
    subscribe_payload = {
        **identity_payload,
        "music_type": "album",
        "total_tracks": 11,
    }

    assert (
        SubscribeStatisticItem(**subscribe_payload).model_dump().items()
        >= subscribe_payload.items()
    )
    assert (
        SubscribeShareItem(**subscribe_payload).model_dump().items()
        >= subscribe_payload.items()
    )
    recognize = MediaRecognizeShareItem(
        keyword="Test",
        type="tv",
        **identity_payload,
    )
    assert recognize.model_dump().items() >= identity_payload.items()
    music_recognize = MediaRecognizeShareItem(
        keyword="Album",
        type="music",
        music_type="album",
        **identity_payload,
    )
    assert music_recognize.music_type == "album"

    plugin_item = SubscribeStatisticItem(
        media_source="plugin-anime",
        media_id="subject-126",
    )
    assert plugin_item.media_source == MediaSource("plugin-anime")

    with pytest.raises(ValidationError):
        SubscribeStatisticItem(
            media_source="invalid source:",
            media_id="subject-126",
        )


@pytest.mark.parametrize(
    "identity_payload",
    [
        {"media_source": "themoviedb"},
        {"media_id": "123"},
        {"media_source": "themoviedb", "media_id": "0"},
        {"media_source": "themoviedb", "media_id": "   "},
    ],
)
def test_schemas_clear_incomplete_or_zero_unified_identity(
        identity_payload: dict,
) -> None:
    """半对身份和零哨兵在模型内部必须统一成空对，避免绕过服务时形成脏载荷。"""
    item = SubscribeStatisticItem(
        name="Invalid Identity",
        type="电影",
        **identity_payload,
    )

    assert item.media_source is None
    assert item.media_id is None
    assert item.storage_payload()["media_source"] is None
    assert item.storage_payload()["media_id"] is None


@pytest.mark.parametrize(
    ("legacy_payload", "expected_source", "expected_id"),
    [
        ({"tmdbid": 101}, MediaSource.TMDB, "101"),
        ({"doubanid": "db-102"}, MediaSource.Douban, "db-102"),
        ({"bangumiid": 103}, MediaSource.Bangumi, "103"),
        ({"anilistid": 104}, MediaSource.AniList, "104"),
        ({"imdbid": "tt0000105"}, MediaSource.IMDb, "tt0000105"),
        ({"tvdbid": 106}, MediaSource.TVDB, "106"),
        ({"mediaid": "tmdb:107"}, MediaSource.TMDB, "107"),
        ({"source": "douban", "mediaid": "db-108"}, MediaSource.Douban, "db-108"),
        ({"tmdbid": 0, "doubanid": "db-109"}, MediaSource.Douban, "db-109"),
        (
            {"media_source": "themoviedb", "media_id": "0", "doubanid": "db-110"},
            MediaSource.Douban,
            "db-110",
        ),
    ],
)
def test_schemas_accept_legacy_client_identity(
        legacy_payload: dict,
        expected_source: MediaSource,
        expected_id: str,
) -> None:
    """旧客户端的专用 ID 和复合 mediaid 应在请求边界转换，不进入存储载荷。"""
    item = SubscribeStatisticItem(
        name="Legacy",
        type="电影",
        **legacy_payload,
    )

    assert item.media_source == expected_source
    assert item.media_id == expected_id
    payload = item.storage_payload()
    assert payload["media_source"] == expected_source
    assert payload["media_id"] == expected_id
    legacy_only_fields = set(legacy_payload) - {"media_source", "media_id"}
    assert not legacy_only_fields.intersection(payload)


def test_unified_identity_takes_priority_over_legacy_fields() -> None:
    """新旧字段同时出现时应以完整统一身份为准，避免旧辅助 ID 覆盖主来源。"""
    item = MediaRecognizeShareItem(
        keyword="Conflict",
        type="tv",
        media_source=MediaSource.Bangumi,
        media_id="primary-42",
        tmdbid=99,
    )

    assert item.media_source == MediaSource.Bangumi
    assert item.media_id == "primary-42"
    assert "tmdbid" not in item.model_dump()


@pytest.mark.parametrize(
    ("source", "media_id", "legacy_field", "legacy_value"),
    [
        (MediaSource.TMDB, "101", "tmdbid", 101),
        (MediaSource.Douban, "db-102", "doubanid", "db-102"),
        (MediaSource.Bangumi, "103", "bangumiid", 103),
        (MediaSource.AniList, "104", "anilistid", 104),
        (MediaSource.IMDb, "tt0000105", "imdbid", "tt0000105"),
        (MediaSource.TVDB, "106", "tvdbid", 106),
    ],
)
def test_legacy_response_fields_are_synthesized_without_persisting_them(
        source: MediaSource,
        media_id: str,
        legacy_field: str,
        legacy_value: str | int,
) -> None:
    """兼容输出应按主来源只合成一个旧 ID 字段，并保留统一身份。"""
    payload = build_legacy_media_identity({
        "media_source": source,
        "media_id": media_id,
        "name": "Compatibility",
    })

    assert payload["media_source"] == source.value
    assert payload["media_id"] == media_id
    assert payload[legacy_field] == legacy_value
    assert payload["mediaid"].endswith(f":{media_id}")
    for other_field in LEGACY_MEDIA_SOURCE_ID_FIELDS.values():
        if other_field != legacy_field:
            assert payload[other_field] is None


def test_statistics_separate_source_namespaces_and_keep_season_zero() -> None:
    """相同数值 ID 的不同数据源和第 0 季必须分别统计。"""

    async def run_scenario() -> None:
        """在隔离的内存数据库中执行多源统计场景。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            for source, media_id in (
                    ("themoviedb", "42"),
                    ("douban", "42"),
                    ("anilist", "42"),
            ):
                result = await SubscribeService.add_subscribe(
                    session,
                    SubscribeStatisticItem(
                        name=f"{source} title",
                        type="电视剧",
                        media_source=source,
                        media_id=media_id,
                        season=0,
                    ),
                )
                assert result["code"] == 0

            records = (
                await session.execute(select(SubscribeStatistics))
            ).scalars().all()
            assert len(records) == 3
            assert {record.media_source for record in records} == {
                "themoviedb", "douban", "anilist",
            }
            assert {record.season for record in records} == {0}
        await engine.dispose()

    asyncio.run(run_scenario())


def test_statistics_keep_album_entity_and_track_count() -> None:
    """专辑订阅统计必须保存实体类型和总曲目数，热门订阅复用时才能保持整专语义。"""

    async def run_scenario() -> None:
        """在隔离数据库中写入并读取专辑订阅统计。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            result = await SubscribeService.add_subscribe(
                session,
                SubscribeStatisticItem(
                    name="叶惠美",
                    type="音乐",
                    media_source="musicbrainz",
                    media_id="release-group-1",
                    music_type="album",
                    total_tracks=11,
                ),
            )
            assert result["code"] == 0
            record = (
                await session.execute(select(SubscribeStatistics))
            ).scalar_one()
            assert record.music_type == "album"
            assert record.total_tracks == 11
        await engine.dispose()

    asyncio.run(run_scenario())


def test_share_keeps_original_enumerated_source_without_tmdb() -> None:
    """非 TMDB 分享应按分享者的枚举来源保存和返回。"""

    async def run_scenario() -> None:
        """在隔离数据库中创建并读取自定义源分享。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            result = await SubscribeShareService.create_share(
                session,
                SubscribeShareItem(
                    share_title="AniList 分享",
                    share_user="tester",
                    name="Anime Movie",
                    type="电影",
                    media_source=MediaSource.AniList,
                    media_id="movie-7",
                ),
            )
            assert result["success"] is True
            shares = await SubscribeShareService.get_shares(session)
            assert shares[0]["media_source"] == "anilist"
            assert shares[0]["media_id"] == "movie-7"
            assert shares[0]["anilistid"] is None
            assert shares[0]["mediaid"] == "anilist:movie-7"
        await engine.dispose()

    asyncio.run(run_scenario())


def test_legacy_statistic_request_stores_unified_identity_and_returns_legacy_fields() -> None:
    """旧版统计请求应写入统一字段，列表响应同时服务新旧客户端。"""

    async def run_scenario() -> None:
        """在隔离数据库中验证旧入参、统一存储与兼容输出。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            result = await SubscribeService.add_subscribe(
                session,
                SubscribeStatisticItem(
                    name="Legacy Movie",
                    type="电影",
                    tmdbid=550,
                ),
            )
            assert result["code"] == 0

            record = (
                await session.execute(select(SubscribeStatistics))
            ).scalar_one()
            assert record.media_source == MediaSource.TMDB.value
            assert record.media_id == "550"

            statistics = await SubscribeService.get_statistics(session, "电影")
            assert statistics[0]["media_source"] == MediaSource.TMDB.value
            assert statistics[0]["media_id"] == "550"
            assert statistics[0]["tmdbid"] == 550
            assert statistics[0]["mediaid"] == "tmdb:550"
        await engine.dispose()

    asyncio.run(run_scenario())


def test_subscribe_apis_keep_legacy_io_while_storing_only_unified_identity() -> None:
    """统计和分享端点应兼容旧输入输出，但数据库模型只持久化统一身份字段。"""

    async def run_scenario() -> None:
        """通过真实路由和隔离数据库验证中心服务的跨版本契约。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async def override_get_db():
            """向测试路由提供隔离的异步数据库会话。"""
            async with session_factory() as session:
                yield session

        cache_manager.statistic_cache.clear()
        cache_manager.share_cache.clear()
        App.dependency_overrides[get_db] = override_get_db
        try:
            transport = ASGITransport(app=App)
            async with AsyncClient(
                    transport=transport,
                    base_url="http://test",
            ) as client:
                statistic_write = await client.post(
                    "/subscribe/add",
                    json={
                        "name": "Legacy Statistic",
                        "type": "电影",
                        "tmdbid": 701,
                    },
                )
                share_write = await client.post(
                    "/subscribe/share",
                    json={
                        "share_title": "Legacy Share",
                        "share_user": "legacy-user",
                        "name": "Legacy Douban",
                        "type": "电影",
                        "doubanid": "db-702",
                    },
                )
                statistic_read = await client.get(
                    "/subscribe/statistic",
                    params={"stype": "电影"},
                )
                share_read = await client.get("/subscribe/shares")

            assert statistic_write.json()["code"] == 0
            assert share_write.json()["success"] is True
            statistic_item = statistic_read.json()[0]
            assert statistic_item["media_source"] == MediaSource.TMDB.value
            assert statistic_item["media_id"] == "701"
            assert statistic_item["tmdbid"] == 701
            assert statistic_item["mediaid"] == "tmdb:701"
            share_item = share_read.json()[0]
            assert share_item["media_source"] == MediaSource.Douban.value
            assert share_item["media_id"] == "db-702"
            assert share_item["doubanid"] == "db-702"
            assert share_item["mediaid"] == "douban:db-702"

            async with session_factory() as session:
                statistic = (
                    await session.execute(select(SubscribeStatistics))
                ).scalar_one()
                share = (
                    await session.execute(select(SubscribeShare))
                ).scalar_one()
                assert (statistic.media_source, statistic.media_id) == (
                    MediaSource.TMDB.value,
                    "701",
                )
                assert (share.media_source, share.media_id) == (
                    MediaSource.Douban.value,
                    "db-702",
                )
                legacy_fields = set(LEGACY_MEDIA_SOURCE_ID_FIELDS.values())
                assert not legacy_fields & {
                    column.name for column in statistic.__table__.columns
                }
                assert not legacy_fields & {
                    column.name for column in share.__table__.columns
                }
        finally:
            App.dependency_overrides.pop(get_db, None)
            cache_manager.statistic_cache.clear()
            cache_manager.share_cache.clear()
            await engine.dispose()

    asyncio.run(run_scenario())


def test_recognize_api_accepts_legacy_request_and_returns_both_contracts() -> None:
    """共享识别公开端点必须允许旧客户端写入，并为新旧客户端同时输出身份。"""

    async def run_scenario() -> None:
        """用内存 Redis 通过 ASGI 端点验证兼容层，而不是只调用内部模型。"""
        storage: dict[str, str] = {}
        redis = Mock()
        redis.get = AsyncMock(side_effect=lambda key: storage.get(key))
        redis.set = AsyncMock(
            side_effect=lambda key, value: storage.__setitem__(key, value)
        )

        with patch(
            "app.services.media_recognize_share.get_redis",
            return_value=redis,
        ):
            transport = ASGITransport(app=App)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                write_response = await client.post(
                    "/recognize/share",
                    json={
                        "keyword": "Legacy API",
                        "type": "tv",
                        "season": 1,
                        "tmdbid": 991,
                    },
                )
                read_response = await client.get(
                    "/recognize/share",
                    params={
                        "keyword": "Legacy API",
                        "type": "tv",
                        "season": 1,
                    },
                )

        assert write_response.status_code == 200
        assert read_response.status_code == 200
        item = read_response.json()["data"]["item"]
        assert item["media_source"] == MediaSource.TMDB.value
        assert item["media_id"] == "991"
        assert item["tmdbid"] == 991
        assert item["mediaid"] == "tmdb:991"

        stored_item = json.loads(next(iter(storage.values())))
        assert stored_item["media_source"] == MediaSource.TMDB.value
        assert stored_item["media_id"] == "991"
        assert "tmdbid" not in stored_item
        assert "mediaid" not in stored_item

    asyncio.run(run_scenario())


@pytest.mark.parametrize(
    "identity_payload",
    [
        {"media_source": "themoviedb"},
        {"media_id": "991"},
        {"media_source": "themoviedb", "media_id": "0"},
    ],
)
def test_recognize_api_rejects_invalid_unified_pair_without_redis_write(
        identity_payload: dict,
) -> None:
    """共享识别端点不得将半对或零哨兵身份写入 Redis。"""

    async def run_scenario() -> None:
        """用真实路由确认非法身份返回明确业务错误且不触发缓存写入。"""
        redis = Mock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock()

        with patch(
                "app.services.media_recognize_share.get_redis",
                return_value=redis,
        ):
            transport = ASGITransport(app=App)
            async with AsyncClient(
                    transport=transport,
                    base_url="http://test",
            ) as client:
                response = await client.post(
                    "/recognize/share",
                    json={
                        "keyword": "Invalid Identity",
                        "type": "movie",
                        **identity_payload,
                    },
                )

        assert response.status_code == 200
        assert response.json() == {
            "code": 1,
            "message": "媒体来源和媒体ID必须同时有效",
        }
        redis.set.assert_not_awaited()

    asyncio.run(run_scenario())


def test_share_keeps_album_entity_and_track_count() -> None:
    """专辑分享写入和读取时不得退化成单曲或丢失整专曲目数。"""

    async def run_scenario() -> None:
        """在隔离数据库中创建并读取音乐专辑分享。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            result = await SubscribeShareService.create_share(
                session,
                SubscribeShareItem(
                    share_title="整张专辑分享",
                    share_user="tester",
                    name="叶惠美",
                    type="音乐",
                    media_source="musicbrainz",
                    media_id="release-group-1",
                    music_type="album",
                    total_tracks=11,
                ),
            )
            assert result["success"] is True
            shares = await SubscribeShareService.get_shares(session)
            assert shares[0]["music_type"] == "album"
            assert shares[0]["total_tracks"] == 11
        await engine.dispose()

    asyncio.run(run_scenario())


def test_media_recognize_share_keeps_enumerated_source_and_season_zero() -> None:
    """共享识别规范化必须保留枚举来源身份和特别季。"""
    item = MediaRecognizeShareService._normalize_item_dict({
        "keyword": "Custom Show",
        "type": "tv",
        "season": 0,
        "media_source": MediaSource.Bangumi,
        "media_id": "show-9",
    })

    assert item["media_source"] == "bangumi"
    assert item["media_id"] == "show-9"
    assert item["season"] == 0
    assert MediaRecognizeShareService._build_cache_key(
        "Custom Show", "tv", season=0
    ).endswith("|0")


def test_media_recognize_share_normalizes_music_type_without_season() -> None:
    """音乐类型应被规范化且永不携带季信息，缓存键与影视类型隔离。"""
    item = MediaRecognizeShareService._normalize_item_dict({
        "keyword": "叶惠美",
        "type": "音乐",
        "media_source": "musicbrainz",
        "media_id": "release-group-1",
        "music_type": "album",
    })

    assert item["type"] == "music"
    assert item["season"] is None
    assert item["media_source"] == "musicbrainz"
    assert item["media_id"] == "release-group-1"
    assert item["music_type"] == "album"
    assert MediaRecognizeShareService._build_cache_key(
        "叶惠美", "music", music_type="album"
    ) == "叶惠美|music|||album"
    assert MediaRecognizeShareService._build_cache_key(
        "叶惠美", "music", music_type="recording"
    ) != MediaRecognizeShareService._build_cache_key(
        "叶惠美", "music", music_type="album"
    )
    assert MediaRecognizeShareService._build_cache_key(
        "叶惠美", "movie"
    ) != MediaRecognizeShareService._build_cache_key("叶惠美", "music")


def test_media_recognize_share_isolates_recording_and_album_keys() -> None:
    """相同标题的单曲与专辑共享记录必须分别写入和查询。"""

    async def run_scenario() -> None:
        """使用内存字典模拟 Redis，验证实体维度不会互相覆盖。"""
        storage: dict[str, str] = {}
        redis = Mock()
        redis.get = AsyncMock(side_effect=lambda key: storage.get(key))
        redis.set = AsyncMock(
            side_effect=lambda key, value: storage.__setitem__(key, value)
        )
        service = MediaRecognizeShareService()

        with patch(
            "app.services.media_recognize_share.get_redis",
            return_value=redis,
        ):
            await service.upsert(MediaRecognizeShareItem(
                keyword="同名作品",
                type="music",
                media_source="musicbrainz",
                media_id="recording-1",
                music_type="recording",
            ))
            await service.upsert(MediaRecognizeShareItem(
                keyword="同名作品",
                type="music",
                media_source="musicbrainz",
                media_id="release-group-1",
                music_type="album",
            ))
            recording = await service.query(
                "同名作品", media_type="music", music_type="recording"
            )
            album = await service.query(
                "同名作品", media_type="music", music_type="album"
            )

        assert recording["data"]["item"]["media_id"] == "recording-1"
        assert album["data"]["item"]["media_id"] == "release-group-1"

    asyncio.run(run_scenario())


def test_media_recognize_share_rewrites_legacy_redis_identity() -> None:
    """命中旧 Redis 专用 ID 记录时应返回并原位写回统一身份。"""

    async def run_scenario() -> None:
        """使用内存字典验证旧缓存转换后不再保留专用 ID。"""
        service = MediaRecognizeShareService()
        cache_key = service._build_cache_key("Legacy", "tv", season=0)
        item_key = service._item_key(cache_key)
        storage = {
            item_key: json.dumps({
                "keyword": "Legacy",
                "type": "tv",
                "season": 0,
                "tmdbid": 123,
                "title": "Legacy Show",
            })
        }
        redis = Mock()
        redis.get = AsyncMock(side_effect=lambda key: storage.get(key))
        redis.set = AsyncMock(
            side_effect=lambda key, value: storage.__setitem__(key, value)
        )

        with patch(
            "app.services.media_recognize_share.get_redis",
            return_value=redis,
        ):
            result = await service.query("Legacy", media_type="tv", season=0)

        assert result["code"] == 0
        item = result["data"]["item"]
        assert item["media_source"] == MediaSource.TMDB.value
        assert item["media_id"] == "123"
        assert item["tmdbid"] == 123
        assert item["mediaid"] == "tmdb:123"
        rewritten = json.loads(storage[item_key])
        assert rewritten["media_source"] == MediaSource.TMDB.value
        assert rewritten["media_id"] == "123"
        assert "tmdbid" not in rewritten
        assert "mediaid" not in rewritten
        redis.set.assert_awaited_once()

    asyncio.run(run_scenario())


def test_schema_upgrade_backfills_identity_and_drops_legacy_columns() -> None:
    """旧 SQLite 数据库应回填统一身份并删除所有专用媒体 ID 列。"""

    async def run_scenario() -> None:
        """构造旧表并执行幂等结构升级。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.execute(text(
                'CREATE TABLE "SUBSCRIBE_STATISTICS" ('
                "id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, type VARCHAR, "
                "media_source VARCHAR, media_id VARCHAR, tmdbid INTEGER, "
                "doubanid VARCHAR, season INTEGER, count INTEGER)"
            ))
            await connection.execute(text(
                'CREATE TABLE "SUBSCRIBE_SHARE" ('
                "id INTEGER PRIMARY KEY, share_title VARCHAR NOT NULL, "
                "name VARCHAR NOT NULL, tmdbid INTEGER, doubanid VARCHAR, "
                "season INTEGER)"
            ))
            await connection.execute(text(
                'INSERT INTO "SUBSCRIBE_STATISTICS" '
                "(id, name, type, doubanid, season, count) "
                "VALUES (1, 'Legacy', '电影', '42', NULL, 1)"
            ))
            await connection.execute(text(
                'INSERT INTO "SUBSCRIBE_STATISTICS" '
                "(id, name, type, tmdbid, doubanid, season, count) "
                "VALUES (4, 'Zero sentinel', '电影', 0, '43', NULL, 1)"
            ))
            await connection.execute(text(
                'INSERT INTO "SUBSCRIBE_STATISTICS" '
                "(id, name, type, media_source, media_id, doubanid, count) "
                "VALUES (5, 'Unified zero sentinel', '电影', 'themoviedb', "
                "'0', '44', 1)"
            ))
            await connection.execute(text(
                'INSERT INTO "SUBSCRIBE_STATISTICS" '
                "(id, name, type, media_source, media_id, tmdbid, count) "
                "VALUES (2, 'Recoverable', '电影', 'plugin-source', "
                "'legacy', 99, 1)"
            ))
            await connection.execute(text(
                'INSERT INTO "SUBSCRIBE_STATISTICS" '
                "(id, name, type, media_source, media_id, count) "
                "VALUES (3, 'Unknown', '电影', 'plugin-source', 'orphan', 1)"
            ))
            await connection.execute(text(
                'INSERT INTO "SUBSCRIBE_STATISTICS" '
                "(id, name, type, media_source, media_id, count) "
                "VALUES (6, 'Source only', '电影', 'douban', NULL, 1)"
            ))
            await connection.execute(text(
                'INSERT INTO "SUBSCRIBE_STATISTICS" '
                "(id, name, type, media_source, media_id, count) "
                "VALUES (7, 'ID only', '电影', NULL, '77', 1)"
            ))
            await connection.execute(text(
                'INSERT INTO "SUBSCRIBE_SHARE" '
                "(id, share_title, name, doubanid, season) "
                "VALUES (1, 'Legacy Share', 'Legacy', '84', NULL)"
            ))

        await ensure_database_schema(engine, Base, is_postgresql=False)
        await ensure_database_schema(engine, Base, is_postgresql=False)

        async with engine.begin() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns(
                        "SUBSCRIBE_STATISTICS"
                    )
                }
            )
            assert {
                "media_source", "media_id", "music_type", "total_tracks",
            } <= columns
            assert not {
                "tmdbid", "doubanid", "bangumiid", "anilistid", "imdbid", "tvdbid",
            }.intersection(columns)
            share_columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns(
                        "SUBSCRIBE_SHARE"
                    )
                }
            )
            assert {"music_type", "total_tracks"} <= share_columns
            assert not {
                "tmdbid", "doubanid", "bangumiid", "anilistid", "imdbid", "tvdbid",
            }.intersection(share_columns)
            row = (
                await connection.execute(text(
                    'SELECT media_source, media_id FROM "SUBSCRIBE_STATISTICS" '
                    "WHERE id = 1"
                ))
            ).one()
            assert tuple(row) == ("douban", "42")
            zero_sentinel_row = (
                await connection.execute(text(
                    'SELECT media_source, media_id FROM "SUBSCRIBE_STATISTICS" '
                    "WHERE id = 4"
                ))
            ).one()
            assert tuple(zero_sentinel_row) == ("douban", "43")
            unified_zero_sentinel_row = (
                await connection.execute(text(
                    'SELECT media_source, media_id FROM "SUBSCRIBE_STATISTICS" '
                    "WHERE id = 5"
                ))
            ).one()
            assert tuple(unified_zero_sentinel_row) == ("douban", "44")
            recovered_row = (
                await connection.execute(text(
                    'SELECT media_source, media_id FROM "SUBSCRIBE_STATISTICS" '
                    "WHERE id = 2"
                ))
            ).one()
            assert tuple(recovered_row) == ("plugin-source", "legacy")
            plugin_row = (
                await connection.execute(text(
                    'SELECT media_source, media_id FROM "SUBSCRIBE_STATISTICS" '
                    "WHERE id = 3"
                ))
            ).one()
            assert tuple(plugin_row) == ("plugin-source", "orphan")
            incomplete_rows = (
                await connection.execute(text(
                    'SELECT media_source, media_id FROM "SUBSCRIBE_STATISTICS" '
                    "WHERE id IN (6, 7) ORDER BY id"
                ))
            ).all()
            assert [tuple(row) for row in incomplete_rows] == [
                (None, None),
                (None, None),
            ]
            share_row = (
                await connection.execute(text(
                    'SELECT media_source, media_id FROM "SUBSCRIBE_SHARE" '
                    "WHERE id = 1"
                ))
            ).one()
            assert tuple(share_row) == ("douban", "84")

            constraint_names = await connection.run_sync(
                lambda sync_connection: {
                    table_name: {
                        constraint.get("name")
                        for constraint in inspect(sync_connection)
                        .get_check_constraints(table_name)
                    }
                    for table_name in (
                        "SUBSCRIBE_STATISTICS", "SUBSCRIBE_SHARE",
                    )
                }
            )
            assert {
                "ck_subscribe_statistics_media_identity",
            } <= constraint_names["SUBSCRIBE_STATISTICS"]
            assert {
                "ck_subscribe_share_media_identity",
            } <= constraint_names["SUBSCRIBE_SHARE"]

        invalid_writes = (
            'INSERT INTO "SUBSCRIBE_STATISTICS" '
            "(id, name, media_source, media_id) "
            "VALUES (20, 'Invalid', 'invalid:source', '20')",
            'UPDATE "SUBSCRIBE_STATISTICS" '
            "SET media_source = 'douban', media_id = '0' WHERE id = 1",
            'INSERT INTO "SUBSCRIBE_SHARE" '
            "(id, share_title, name, media_source, media_id) "
            "VALUES (20, 'Invalid', 'Invalid', 'douban', NULL)",
        )
        for statement in invalid_writes:
            with pytest.raises(IntegrityError):
                async with engine.begin() as connection:
                    await connection.execute(text(statement))
        await engine.dispose()

    asyncio.run(run_scenario())


def test_schema_upgrade_replaces_fixed_sqlite_source_constraint() -> None:
    """已存在固定来源 CHECK 的 SQLite 表升级后应允许新插件来源。"""

    async def run_scenario() -> None:
        """构造旧约束表并验证升级后的数据与写入契约。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.execute(text(
                'CREATE TABLE "SUBSCRIBE_STATISTICS" ('
                "id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, type VARCHAR, "
                "media_source VARCHAR, media_id VARCHAR, season INTEGER, "
                "count INTEGER, CONSTRAINT "
                "ck_subscribe_statistics_media_identity CHECK ("
                "(media_source IS NULL AND media_id IS NULL) OR "
                "(media_source IN ('themoviedb', 'douban') "
                "AND media_id IS NOT NULL AND trim(media_id) <> '' "
                "AND trim(media_id) <> '0')))"
            ))
            await connection.execute(text(
                'INSERT INTO "SUBSCRIBE_STATISTICS" '
                "(id, name, media_source, media_id, count) "
                "VALUES (1, 'Existing', 'themoviedb', '550', 1)"
            ))

        await ensure_database_schema(engine, Base, is_postgresql=False)

        async with engine.begin() as connection:
            await connection.execute(text(
                'INSERT INTO "SUBSCRIBE_STATISTICS" '
                "(id, name, media_source, media_id, count) "
                "VALUES (2, 'Plugin', 'acme.video', 'custom-2', 1)"
            ))
            rows = (
                await connection.execute(text(
                    'SELECT media_source, media_id FROM "SUBSCRIBE_STATISTICS" '
                    "ORDER BY id"
                ))
            ).all()
        assert [tuple(row) for row in rows] == [
            ("themoviedb", "550"),
            ("acme.video", "custom-2"),
        ]
        await engine.dispose()

    asyncio.run(run_scenario())


@pytest.mark.parametrize(
    ("table_name", "required_columns", "constraint_name"),
    [
        (
            "SUBSCRIBE_STATISTICS",
            "name",
            "ck_subscribe_statistics_media_identity",
        ),
        (
            "SUBSCRIBE_SHARE",
            "share_title, name",
            "ck_subscribe_share_media_identity",
        ),
    ],
)
def test_new_tables_enforce_media_identity_check_constraint(
        table_name: str,
        required_columns: str,
        constraint_name: str,
) -> None:
    """新建订阅表必须允许插件来源，并拒绝非法来源、半对和无效 ID。"""

    async def run_scenario() -> None:
        """在真实 SQLite 元数据和写入路径上验证约束语义。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            check_names = await connection.run_sync(
                lambda sync_connection: {
                    constraint.get("name")
                    for constraint in inspect(sync_connection).get_check_constraints(
                        table_name
                    )
                }
            )
        assert constraint_name in check_names

        required_values = ", ".join("'Valid'" for _ in required_columns.split(", "))
        valid_identities = (
            (None, None),
            (MediaSource.TMDB.value, "550"),
            ("plugin-source", "plugin-550"),
        )
        for row_id, (media_source, media_id) in enumerate(valid_identities, start=1):
            async with engine.begin() as connection:
                await connection.execute(text(
                    f'INSERT INTO "{table_name}" '
                    f'(id, {required_columns}, media_source, media_id) '
                    f'VALUES (:id, {required_values}, :media_source, :media_id)'
                ), {
                    "id": row_id,
                    "media_source": media_source,
                    "media_id": media_id,
                })

        invalid_identities = (
            (MediaSource.TMDB.value, None),
            (None, "550"),
            ("invalid:source", "550"),
            (MediaSource.TMDB.value, ""),
            (MediaSource.TMDB.value, "   "),
            (MediaSource.TMDB.value, "0"),
            (MediaSource.TMDB.value, " 0 "),
        )
        for row_id, (media_source, media_id) in enumerate(
                invalid_identities,
                start=10,
        ):
            with pytest.raises(IntegrityError):
                async with engine.begin() as connection:
                    await connection.execute(text(
                        f'INSERT INTO "{table_name}" '
                        f'(id, {required_columns}, media_source, media_id) '
                        f'VALUES (:id, {required_values}, :media_source, :media_id)'
                    ), {
                        "id": row_id,
                        "media_source": media_source,
                        "media_id": media_id,
                    })
        await engine.dispose()

    asyncio.run(run_scenario())
