"""反思调度器 (a4 §2)。

程序运行期间按节律触发反思任务。随 runtime 启动，程序关闭即停（不补跑，N20）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from cairn.memory.store import MemoryStore
from cairn.reflection.tasks import (
    decay_all_nodes,
    process_write_queue,
    prune_all_edges,
    scan_self_defining,
    update_self_portrait,
)

logger = logging.getLogger(__name__)

# 调度间隔
_TICK_SECONDS: float = 60.0

# 任务间隔
_DAILY_INTERVAL: timedelta = timedelta(hours=24)
_WEEKLY_INTERVAL: timedelta = timedelta(days=7)


class ReflectionScheduler:
    """反思后台调度器 (a4 §2)。

    用法:
        scheduler = ReflectionScheduler(store, call_llm=my_llm)
        await scheduler.start()
        # ... 程序运行 ...
        await scheduler.stop()
    """

    def __init__(
        self,
        store: MemoryStore,
        call_llm: Callable[[str, str], Awaitable[str]] | None = None,
    ) -> None:
        self._store = store
        self._call_llm = call_llm
        self._task: asyncio.Task[Any] | None = None
        self._running: bool = False
        self._last_daily: datetime | None = None
        self._last_weekly: datetime | None = None

        # 从 metrics 恢复上次运行时间（防止重启后立即触发）
        self._last_daily = self._read_last_run("last_daily_reflection")
        self._last_weekly = self._read_last_run("last_weekly_reflection")

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """启动后台调度循环。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Reflection scheduler started (tick=%.0fs, daily=24h, weekly=7d)", _TICK_SECONDS)

    async def stop(self) -> None:
        """停止调度器。不补跑已过去的任务（N20）。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Reflection scheduler stopped")

    # ============================================================
    # 调度循环
    # ============================================================

    async def _loop(self) -> None:
        """主循环：每 _TICK_SECONDS 检查一次是否该执行任务。"""
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                if self._should_run_daily(now):
                    await self._execute_daily(now)
                if self._should_run_weekly(now):
                    await self._execute_weekly(now)
            except Exception:
                logger.warning("Reflection tick failed", exc_info=True)
            await asyncio.sleep(_TICK_SECONDS)

    def _should_run_daily(self, now: datetime) -> bool:
        if self._last_daily is None:
            return True
        return (now - self._last_daily) >= _DAILY_INTERVAL

    def _should_run_weekly(self, now: datetime) -> bool:
        if self._last_weekly is None:
            return True
        return (now - self._last_weekly) >= _WEEKLY_INTERVAL

    # ============================================================
    # 任务执行
    # ============================================================

    async def _execute_daily(self, now: datetime) -> None:
        """执行每日任务：衰减 + 边修剪 + 写回队列处理。"""
        logger.info("Running daily reflection tasks...")
        try:
            removed = decay_all_nodes(self._store)
            pruned = prune_all_edges(self._store)
            processed = process_write_queue(self._store)

            self._last_daily = now
            self._write_last_run("last_daily_reflection", now)

            logger.info(
                "Daily reflection done: decay removed %s, pruned %s edges, processed %d queue entries",
                removed, pruned, processed,
            )
        except Exception:
            logger.warning("Daily reflection failed", exc_info=True)

    async def _execute_weekly(self, now: datetime) -> None:
        """执行每周任务：self-defining 扫描 + 自画像更新。"""
        logger.info("Running weekly reflection tasks...")
        try:
            # Self-defining 扫描（无 LLM）
            updated = scan_self_defining(self._store)
            logger.info("Self-defining scan: %d nodes newly marked", updated)

            # 自画像更新（需 LLM）
            if self._call_llm:
                portrait = await update_self_portrait(self._store, self._call_llm)
                if portrait:
                    logger.info("Self-portrait updated (%d chars)", len(portrait.content))

            self._last_weekly = now
            self._write_last_run("last_weekly_reflection", now)
        except Exception:
            logger.warning("Weekly reflection failed, will retry next tick", exc_info=True)

    # ============================================================
    # 上次运行时间持久化
    # ============================================================

    def _read_last_run(self, metric_name: str) -> datetime | None:
        try:
            rows = self._store._conn.execute(
                "SELECT created_at FROM metrics WHERE metric_name = ? ORDER BY created_at DESC LIMIT 1",
                (metric_name,),
            ).fetchall()
            if rows:
                return self._store._parse_dt(rows[0]["created_at"])
        except Exception:
            pass
        return None

    def _write_last_run(self, metric_name: str, now: datetime) -> None:
        try:
            self._store._conn.execute(
                "INSERT INTO metrics (session_id, metric_name, metric_value, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("reflection", metric_name, 1.0, "{}", now.isoformat()),
            )
            self._store._conn.commit()
        except Exception:
            pass
