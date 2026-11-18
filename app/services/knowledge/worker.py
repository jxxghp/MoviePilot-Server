"""
独立知识索引和导入执行器；不从 Web 生命周期启动，不输出知识或原文异常。
"""

import argparse
import asyncio
import contextlib
import logging
import signal
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.services.knowledge import indexes, jobs
from app.services.knowledge.imports import catalog as import_catalog, files as import_files, worker as import_worker
from app.services.knowledge.retrieval import storage as retrieval_storage

logger = logging.getLogger(__name__)
BUILD_TIMEOUT_SECONDS = 600


class KnowledgeWorker:
    """
    单进程顺序构建，多个进程共享数据库租约，失联执行器不能激活旧产物。
    """

    def __init__(self, sessions, directory: Path):
        """
        注入会话工厂和已校验的私有目录，测试不连接真实 SQL 或 Redis。
        """
        self.sessions = sessions
        self.directory = directory

    async def renew(self, lease: jobs.JobLease, stopped: asyncio.Event) -> None:
        """
        定期续租；失效、取消或数据库故障时通知构建中止，禁止带病激活。
        """
        while True:
            await asyncio.sleep(jobs.LEASE_SECONDS / 3)
            try:
                async with self.sessions() as db:
                    valid = await jobs.heartbeat(db, lease)
                    await db.commit()
            except SQLAlchemyError:
                valid = False
            if not valid:
                stopped.set()
                return

    async def build(self, lease: jobs.JobLease) -> indexes.SnapshotBuilder | dict:
        """
        同时监视构建和租约，及时停止已被取消或失去所有权的构建。
        """
        stopped = asyncio.Event()
        renewal = asyncio.create_task(self.renew(lease, stopped))
        lost = asyncio.create_task(stopped.wait())
        operation = (indexes.build_snapshot(self.sessions, self.directory, lease.generation) if lease.kind == "index"
                     else import_worker.execute(self.sessions, import_files.import_directory(self.directory), lease))
        building = asyncio.create_task(operation)
        retained = False
        try:
            done, _pending = await asyncio.wait((building, lost), return_when=asyncio.FIRST_COMPLETED)
            if lost in done and building not in done:
                raise RuntimeError("lease_lost")
            result = await building
            retained = True
            return result
        finally:
            for task in (renewal, lost, building):
                if not task.done():
                    task.cancel()
            await asyncio.gather(renewal, lost, building, return_exceptions=True)
            if not retained and not building.cancelled() and building.exception() is None:
                result = building.result()
                if isinstance(result, indexes.SnapshotBuilder):
                    result.destination.unlink(missing_ok=True)

    async def run_once(self) -> dict | None:
        """
        只在在线开关启用时执行一个任务；成功构建与成功激活是不同结果。
        """
        if not settings.KNOWLEDGE_ENABLED:
            return None
        async with self.sessions() as db:
            await import_catalog.cleanup(db, import_files.import_directory(self.directory))
        async with self.sessions() as db:
            await retrieval_storage.collect(db, self.directory)
        async with self.sessions() as db:
            lease = await jobs.claim(db)
            await db.commit()
        if lease is None:
            return None
        builder = None
        activation_attempted = False
        try:
            async with asyncio.timeout(BUILD_TIMEOUT_SECONDS):
                builder = await self.build(lease)
            if lease.kind == "import":
                if builder["outcome"] in {"imported", "imported_with_skips"}:
                    with contextlib.suppress(OSError, SQLAlchemyError):
                        async with self.sessions() as db:
                            await import_catalog.cleanup(db, import_files.import_directory(self.directory))
                return builder
            async with self.sessions() as db:
                activation_attempted = True
                outcome = await jobs.finish_index(db, lease, builder.snapshot, builder.documents, builder.chunk_count)
                await db.commit()
            if outcome != "activated":
                builder.destination.unlink(missing_ok=True)
            return {"job_id": lease.job_id, "outcome": outcome}
        except asyncio.CancelledError:
            if isinstance(builder, indexes.SnapshotBuilder) and not activation_attempted:
                builder.destination.unlink(missing_ok=True)
            # 进程强停保留租约，恢复者只能在租约到期后重新认领。
            raise
        except Exception as error:
            if isinstance(builder, indexes.SnapshotBuilder) and not activation_attempted:
                builder.destination.unlink(missing_ok=True)
            # 激活提交失去回执时可能已成功，保留完整文件，不能删掉 SQL 已指向的快照。
            code = ("timeout" if isinstance(error, TimeoutError) else
                    "fts_unavailable" if isinstance(error, indexes.FtsUnavailable) else
                    "database_failed" if isinstance(error, SQLAlchemyError) else
                    "storage_failed" if isinstance(error, OSError) else
                    "import_failed" if lease.kind == "import" else "index_failed")
            async with self.sessions() as db:
                outcome = await jobs.fail_job(db, lease, code)
                await db.commit()
            return {"job_id": lease.job_id, "outcome": outcome}


async def serve(*, once: bool = False) -> None:
    """
    独立入口初始化结构后执行任务，SIGTERM 停止继续认领并等待当前有界构建结束。
    """
    from app.db.database import AsyncSessionLocal, engine
    from app.models import Base
    from app.services.database_schema import ensure_database_schema

    directory = indexes.private_directory(settings.KNOWLEDGE_DATA_DIR)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(event, stop.set)
    try:
        await ensure_database_schema(engine, Base, settings.is_postgresql)
        worker = KnowledgeWorker(AsyncSessionLocal, directory)
        while not stop.is_set():
            result = await worker.run_once()
            if result:
                logger.info("Knowledge job %s: %s", result["job_id"], result["outcome"])
            if once:
                return
            if result is None or result["outcome"] not in {"activated", "superseded"}:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=5)
    finally:
        for event in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(event)
        await engine.dispose()


def main() -> None:
    """
    仅提供固定执行选项，错误日志不包含异常载荷、路径或知识内容。
    """
    parser = argparse.ArgumentParser(description="MoviePilot 中心知识独立 worker")
    parser.add_argument("--once", action="store_true", help="最多认领一个已到期任务后退出")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        asyncio.run(serve(once=arguments.once))
    except Exception:
        logger.error("Knowledge worker stopped: check private storage and database configuration")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
