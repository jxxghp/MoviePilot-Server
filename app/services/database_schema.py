"""
数据库结构初始化服务
"""
from typing import Any

from sqlalchemy import CheckConstraint, MetaData, Table, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.media import (
    MEDIA_SOURCE_ALIASES as MEDIA_SOURCE_ENUM_ALIASES,
    media_identity_check_sql,
)

# 跨版本启动必须竞争同一把锁，避免滚动发布期间并发修改订阅表结构。
POSTGRESQL_SCHEMA_LOCK_ID = 2026052501

SUBSCRIBE_UNIFIED_COLUMNS = {
    "SUBSCRIBE_STATISTICS": {
        "media_source": "VARCHAR",
        "media_id": "VARCHAR",
        "music_type": "VARCHAR",
        "total_tracks": "INTEGER",
    },
    "SUBSCRIBE_SHARE": {
        "media_source": "VARCHAR",
        "media_id": "VARCHAR",
        "music_type": "VARCHAR",
        "total_tracks": "INTEGER",
    },
}

LEGACY_MEDIA_ID_COLUMNS = (
    ("themoviedb", "tmdbid"),
    ("douban", "doubanid"),
    ("bangumi", "bangumiid"),
    ("anilist", "anilistid"),
    ("imdb", "imdbid"),
    ("tvdb", "tvdbid"),
)

MEDIA_SOURCE_ALIASES = {
    alias: source.value for alias, source in MEDIA_SOURCE_ENUM_ALIASES.items()
}
MEDIA_IDENTITY_INDEXES = {
    "SUBSCRIBE_STATISTICS": {
        "ix_SUBSCRIBE_STATISTICS_media_source": ("media_source",),
        "ix_SUBSCRIBE_STATISTICS_media_id": ("media_id",),
        "ix_subscribe_statistics_media_identity": (
            "media_source", "media_id", "season",
        ),
    },
    "SUBSCRIBE_SHARE": {
        "ix_SUBSCRIBE_SHARE_media_source": ("media_source",),
        "ix_SUBSCRIBE_SHARE_media_id": ("media_id",),
        "ix_subscribe_share_media_identity": (
            "media_source", "media_id", "season",
        ),
    },
}

MEDIA_IDENTITY_CONSTRAINTS = {
    "SUBSCRIBE_STATISTICS": "ck_subscribe_statistics_media_identity",
    "SUBSCRIBE_SHARE": "ck_subscribe_share_media_identity",
}


def _sqlite_rebuild_identity_table(
        connection: Connection,
        table_name: str,
        constraint_name: str,
) -> None:
    """重建 SQLite 存量表，以替换无法原地修改的身份 CHECK 约束。"""
    preparer = connection.dialect.identifier_preparer
    quoted_table = preparer.quote(table_name)
    temporary_table = f"__mp_{table_name.lower()}_identity_old"
    quoted_temporary = preparer.quote(temporary_table)
    if temporary_table in inspect(connection).get_table_names():
        raise RuntimeError(f"SQLite migration table already exists: {temporary_table}")

    # SQLite 重命名会连带保留索引和触发器名称，先保存定义再释放名称。
    schema_objects = connection.execute(text(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE tbl_name = :table_name AND type IN ('index', 'trigger') "
        "AND sql IS NOT NULL"
    ), {"table_name": table_name}).mappings().all()
    identity_trigger_prefix = constraint_name.removeprefix("ck_")
    obsolete_triggers = {
        f"trg_{identity_trigger_prefix}_insert",
        f"trg_{identity_trigger_prefix}_update",
    }

    reflected_metadata = MetaData()
    reflected_table = Table(
        table_name,
        reflected_metadata,
        autoload_with=connection,
    )
    replacement_metadata = MetaData()
    replacement_table = reflected_table.to_metadata(replacement_metadata)
    for constraint in tuple(replacement_table.constraints):
        constraint_sql = str(getattr(constraint, "sqltext", "")).lower()
        if isinstance(constraint, CheckConstraint) and (
                constraint.name == constraint_name
                or (
                    "media_source" in constraint_sql
                    and "media_id" in constraint_sql
                )
        ):
            replacement_table.constraints.remove(constraint)
    replacement_table.append_constraint(CheckConstraint(
        media_identity_check_sql(),
        name=constraint_name,
    ))
    # 索引按 sqlite_master 原始 SQL 重建，可保留部分索引等方言细节。
    replacement_table.indexes.clear()

    for schema_object in schema_objects:
        object_name = preparer.quote(schema_object["name"])
        connection.execute(text(
            f'DROP {schema_object["type"].upper()} IF EXISTS {object_name}'
        ))
    connection.execute(text(
        f"ALTER TABLE {quoted_table} RENAME TO {quoted_temporary}"
    ))
    replacement_table.create(connection)
    quoted_columns = ", ".join(
        preparer.quote(column.name) for column in reflected_table.columns
    )
    connection.execute(text(
        f"INSERT INTO {quoted_table} ({quoted_columns}) "
        f"SELECT {quoted_columns} FROM {quoted_temporary}"
    ))
    connection.execute(text(f"DROP TABLE {quoted_temporary}"))

    for schema_object in schema_objects:
        if (
                schema_object["type"] == "trigger"
                and schema_object["name"] in obsolete_triggers
        ):
            continue
        connection.execute(text(schema_object["sql"]))


def _ensure_media_identity_constraints(
        connection: Connection,
        table_names: set[str],
) -> None:
    """为存量订阅表补齐数据库级统一媒体身份原子约束。"""
    dialect_name = connection.dialect.name
    for table_name, constraint_name in MEDIA_IDENTITY_CONSTRAINTS.items():
        if table_name not in table_names:
            continue
        if dialect_name == "postgresql":
            existing_constraints = {
                constraint.get("name"): constraint.get("sqltext", "")
                for constraint in inspect(connection).get_check_constraints(table_name)
            }
            constraint_sql = existing_constraints.get(constraint_name, "")
            normalized_constraint_sql = constraint_sql.lower()
            if "length" in normalized_constraint_sql and "64" in normalized_constraint_sql:
                continue
            if constraint_name in existing_constraints:
                connection.execute(text(
                    f'ALTER TABLE "{table_name}" '
                    f'DROP CONSTRAINT "{constraint_name}"'
                ))
            connection.execute(text(
                f'ALTER TABLE "{table_name}" '
                f'ADD CONSTRAINT "{constraint_name}" '
                f'CHECK ({media_identity_check_sql()})'
            ))
            continue

        if dialect_name != "sqlite":
            continue
        existing_constraints = {
            constraint.get("name"): constraint.get("sqltext", "")
            for constraint in inspect(connection).get_check_constraints(table_name)
        }
        constraint_sql = existing_constraints.get(constraint_name, "").lower()
        if "length" in constraint_sql and "64" in constraint_sql:
            continue
        _sqlite_rebuild_identity_table(
            connection,
            table_name,
            constraint_name,
        )


def _ensure_subscribe_identity_schema(connection: Connection) -> None:
    """迁移订阅表身份字段：补列、回填、规范来源并删除旧专用 ID 列。"""
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    for table_name, columns in SUBSCRIBE_UNIFIED_COLUMNS.items():
        if table_name not in table_names:
            continue
        existing_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        for column_name, column_type in columns.items():
            if column_name not in existing_columns:
                connection.execute(text(
                    f'ALTER TABLE "{table_name}" '
                    f'ADD COLUMN "{column_name}" {column_type}'
                ))

    for table_name in SUBSCRIBE_UNIFIED_COLUMNS:
        if table_name not in table_names:
            continue
        existing_columns = {
            column["name"] for column in inspect(connection).get_columns(table_name)
        }

        for alias, source in MEDIA_SOURCE_ALIASES.items():
            connection.execute(text(
                f'UPDATE "{table_name}" SET media_source = :source '
                f'WHERE LOWER(TRIM(media_source)) = :alias '
                'AND media_source <> :source'
            ), {"source": source, "alias": alias})

        connection.execute(text(
            f'UPDATE "{table_name}" '
            'SET media_source = LOWER(TRIM(media_source)) '
            # 只改写尚未规范的行，避免每次启动重写全表
            'WHERE media_source IS NOT NULL '
            'AND media_source <> LOWER(TRIM(media_source))'
        ))

        invalid_identity_sql = (
            "media_source IS NULL OR TRIM(media_source) = '' "
            "OR media_id IS NULL OR TRIM(media_id) IN ('', '0') "
            "OR LENGTH(media_source) > 64 OR media_source LIKE '%:%' "
            "OR media_source LIKE '% %'"
        )
        for source, id_field in LEGACY_MEDIA_ID_COLUMNS:
            if id_field not in existing_columns:
                continue
            connection.execute(text(
                f'UPDATE "{table_name}" '
                f'SET media_source = :source, '
                f'media_id = TRIM(CAST("{id_field}" AS VARCHAR)) '
                f'WHERE ({invalid_identity_sql}) '
                f'AND "{id_field}" IS NOT NULL '
                f'AND TRIM(CAST("{id_field}" AS VARCHAR)) NOT IN (\'\', \'0\')'
            ), {"source": source})

        # 统一身份必须成对存在且来源标识合法；无法从旧列补齐的身份不再保留。
        connection.execute(text(
            f'UPDATE "{table_name}" '
            'SET media_source = NULL, media_id = NULL '
            f'WHERE {invalid_identity_sql}'
        ))
        connection.execute(text(
            f'UPDATE "{table_name}" SET media_id = TRIM(media_id) '
            'WHERE media_id IS NOT NULL'
        ))

        legacy_columns = {
            column for _, column in LEGACY_MEDIA_ID_COLUMNS
            if column in existing_columns
        }
        if legacy_columns:
            for index in inspect(connection).get_indexes(table_name):
                index_name = index.get("name")
                index_columns = set(index.get("column_names") or [])
                if index_name and index_columns.intersection(legacy_columns):
                    connection.execute(text(
                        f'DROP INDEX IF EXISTS "{index_name}"'
                    ))
            for column_name in legacy_columns:
                connection.execute(text(
                    f'ALTER TABLE "{table_name}" '
                    f'DROP COLUMN "{column_name}"'
                ))

    for table_name, indexes in MEDIA_IDENTITY_INDEXES.items():
        if table_name not in table_names:
            continue
        existing_indexes = {
            index["name"] for index in inspect(connection).get_indexes(table_name)
        }
        for index_name, columns in indexes.items():
            if index_name in existing_indexes:
                continue
            column_sql = ", ".join(f'"{column}"' for column in columns)
            connection.execute(text(
                f'CREATE INDEX IF NOT EXISTS "{index_name}" '
                f'ON "{table_name}" ({column_sql})'
            ))

    _ensure_media_identity_constraints(connection, table_names)

async def ensure_database_schema(engine: AsyncEngine, base: Any, is_postgresql: bool) -> None:
    """
    确保当前数据库中存在所有已注册模型表。
    """
    if is_postgresql:
        await ensure_postgresql_schema(engine, base)
        return

    async with engine.begin() as conn:
        await conn.run_sync(base.metadata.create_all)
        await conn.run_sync(_ensure_subscribe_identity_schema)


async def ensure_postgresql_schema(engine: AsyncEngine, base: Any) -> None:
    """
    在PostgreSQL事务级锁保护下创建所有已注册模型表。
    """
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": POSTGRESQL_SCHEMA_LOCK_ID},
        )
        await conn.run_sync(base.metadata.create_all)
        await conn.run_sync(_ensure_subscribe_identity_schema)
