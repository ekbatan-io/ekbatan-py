#!/usr/bin/env python3
"""Native async against today's answer for an async web app, on one deposit workload.

Today an async endpoint (FastAPI, Starlette) calls the sync executor through a thread pool -
``await anyio.to_thread.run_sync(...)``, 40 threads by default. ``ekbatan.aio`` awaits the
database instead. This runs the same deposits both ways and reports what each one costs:

- **sync-threads**: ``ActionExecutor`` on psycopg, called with ``asyncio.to_thread`` from an
  event loop whose default executor has 40 threads - anyio's default limit, without anyio;
- **async-asyncpg**: ``AsyncActionExecutor`` on asyncpg (``ekbatan[asyncpg]``);
- **async-psycopg**: ``AsyncActionExecutor`` on psycopg's own async mode - no extra driver.

Each deposit reads the wallet, then commits the update and its outbox row in one transaction.
``N`` workers run at once, each on its own wallet, so nothing conflicts; together they do
``--total`` deposits. Every engine gets the same pool (``--pool``). A heartbeat task asks to
wake every 10ms the whole time; how often it actually woke, and its worst lateness, say
whether the event loop stayed free to serve anything else.

Starts a throwaway PostgreSQL container and removes it at the end. Needs Docker.

    uv run python scripts/bench-async.py
    uv run python scripts/bench-async.py --levels 1,50,200,1000 --total 2000 --pool 40
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from ekbatan import SystemClock  # noqa: E402
from ekbatan.action import ActionExecutor  # noqa: E402
from ekbatan.aio import AsyncActionExecutor, AsyncDatabaseRegistry  # noqa: E402
from ekbatan.config import ShardingConfig, split_login  # noqa: E402
from ekbatan.ids import ShardedId  # noqa: E402
from ekbatan.migrations import migrate  # noqa: E402
from ekbatan.persistence import DatabaseRegistry  # noqa: E402
from fixtures.actions import (  # noqa: E402
    AsyncDepositMoneyAction,
    DepositMoneyAction,
    DepositParams,
)
from fixtures.domain import NOW, Wallet  # noqa: E402
from fixtures.repositories import AsyncWalletRepository, WalletRepository  # noqa: E402
from fixtures.schema import EVENTS, WALLETS  # noqa: E402

TICK = 0.010


def layout(url: str, driver: str, pool: int) -> ShardingConfig:
    plain, username, password = split_login(url)
    _, _, rest = plain.partition("://")
    return ShardingConfig.single(
        f"postgresql+{driver}://{rest}",
        username=username,
        password=password,
        pool_size=pool,
        pool_timeout=120,
    )


def fresh_wallets(n: int) -> list[Wallet]:
    return [
        Wallet.open(
            id=ShardedId[Wallet].random(),
            owner_id=uuid4(),
            currency="EUR",
            balance=Decimal("0"),
            created_date=NOW,
        )
        for _ in range(n)
    ]


async def heartbeat(stop: asyncio.Event, out: dict[str, float]) -> None:
    """Ask to wake every TICK; count the wake-ups and the worst lateness."""
    ticks, worst = 0, 0.0
    expected = time.perf_counter() + TICK
    while not stop.is_set():
        await asyncio.sleep(max(0.0, expected - time.perf_counter()))
        now = time.perf_counter()
        worst = max(worst, now - expected)
        ticks += 1
        expected = max(expected + TICK, now)
    out["ticks"], out["worst_lag"] = ticks, worst


async def drive(n: int, total: int, one: Any, wallets: list[Wallet]) -> dict[str, Any]:
    """``n`` workers at once, each depositing ``total // n`` times into its own wallet."""
    each = max(1, total // n)
    latencies: list[float] = []

    async def worker(wallet: Wallet) -> None:
        for _ in range(each):
            params = DepositParams(wallet_id=wallet.id, amount=Decimal("1"), recipient="x")
            t0 = time.perf_counter()
            await one(params)
            latencies.append(time.perf_counter() - t0)

    stop, beat = asyncio.Event(), {}
    pulse = asyncio.create_task(heartbeat(stop, beat))
    gc.collect()
    t0 = time.perf_counter()
    await asyncio.gather(*(worker(w) for w in wallets[:n]))
    elapsed = time.perf_counter() - t0
    stop.set()
    await pulse
    latencies.sort()
    return {
        "n": n,
        "ops": len(latencies),
        "elapsed": elapsed,
        "throughput": len(latencies) / elapsed,
        "p50": statistics.median(latencies) * 1000,
        "p99": latencies[min(len(latencies) - 1, int(len(latencies) * 0.99))] * 1000,
        "ticks": beat["ticks"],
        "expected_ticks": int(elapsed / TICK),
        "worst_lag": beat["worst_lag"] * 1000,
    }


async def run_sync(url: str, levels: list[int], total: int, pool: int, threads: int) -> list[Any]:
    registry = DatabaseRegistry.from_config(layout(url, "psycopg", pool))
    clock = SystemClock()
    repo = WalletRepository(Wallet, WALLETS, registry)
    executor = ActionExecutor(
        namespace="bench.wallet",
        registry=registry,
        repositories=[repo],
        actions=[DepositMoneyAction(repo, clock)],
        events=EVENTS,
        clock=clock,
    )
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(threads))
    wallets = fresh_wallets(max(levels))
    repo.add_all_no_result(wallets)

    async def one(params: DepositParams) -> None:
        await asyncio.to_thread(executor.execute, DepositMoneyAction, params)

    await drive(min(10, max(levels)), 100, one, wallets)  # warm the pool and the caches
    try:
        return [await drive(n, total, one, wallets) for n in levels]
    finally:
        registry.close()


async def run_async(url: str, driver: str, levels: list[int], total: int, pool: int) -> list[Any]:
    registry = AsyncDatabaseRegistry.from_config(layout(url, driver, pool))
    clock = SystemClock()
    repo = AsyncWalletRepository(Wallet, WALLETS, registry)
    executor = AsyncActionExecutor(
        namespace="bench.wallet",
        registry=registry,
        repositories=[repo],
        actions=[AsyncDepositMoneyAction(repo, clock)],
        events=EVENTS,
        clock=clock,
    )
    wallets = fresh_wallets(max(levels))
    await repo.add_all_no_result(wallets)

    async def one(params: DepositParams) -> None:
        await executor.execute(AsyncDepositMoneyAction, params)

    await drive(min(10, max(levels)), 100, one, wallets)
    try:
        return [await drive(n, total, one, wallets) for n in levels]
    finally:
        await registry.aclose()


def table(title: str, rows: list[dict[str, Any]]) -> str:
    out = [
        f"### {title}",
        "",
        "| N | ops | ops/s | p50 ms | p99 ms | heartbeat ticks (of ideal) | worst tick lag ms |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        share = 100 * r["ticks"] / max(1, r["expected_ticks"])
        out.append(
            f"| {r['n']} | {r['ops']} | {r['throughput']:.0f} | {r['p50']:.1f} | "
            f"{r['p99']:.1f} | {r['ticks']} ({share:.0f}%) | {r['worst_lag']:.1f} |"
        )
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--levels", default="1,50,200,1000")
    parser.add_argument("--total", type=int, default=2000)
    parser.add_argument("--pool", type=int, default=40)
    parser.add_argument("--threads", type=int, default=40)
    parser.add_argument("--image", default="postgres:17-alpine")
    args = parser.parse_args()
    levels = [int(x) for x in args.levels.split(",")]

    from testcontainers.community.postgres import PostgresContainer

    container = PostgresContainer(args.image, driver="psycopg")
    # Enough server connections for one pool at a time plus the migrator.
    container.with_command("postgres -c max_connections=200")
    container.start()
    try:
        url = container.get_connection_url()
        plain, username, password = split_login(url)
        migrate(
            ShardingConfig.single(plain, username=username, password=password),
            ROOT / "tests" / "migrations" / "postgres",
        )
        print(f"python {sys.version.split()[0]}, pool {args.pool}, threads {args.threads}")
        results = {
            "sync executor in a 40-thread pool (psycopg)": asyncio.run(
                run_sync(url, levels, args.total, args.pool, args.threads)
            ),
            "AsyncActionExecutor (asyncpg)": asyncio.run(
                run_async(url, "asyncpg", levels, args.total, args.pool)
            ),
            "AsyncActionExecutor (psycopg async)": asyncio.run(
                run_async(url, "psycopg", levels, args.total, args.pool)
            ),
        }
        for title, rows in results.items():
            print()
            print(table(title, rows))
    finally:
        container.stop()


if __name__ == "__main__":
    main()
