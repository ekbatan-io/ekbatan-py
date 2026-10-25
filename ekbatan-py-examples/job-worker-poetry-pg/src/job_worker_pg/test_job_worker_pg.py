"""The worker's own test, against two real Postgres servers - the global shard and the Mexico
shard, as the worker runs in production.

    make

It asks what the Java example's test asks: the stipend job tops up the under-funded wallet on
each shard and its deposits leave notifications, and the driver settings in `ekbatan.yaml`
reach the database. Two checks are its own: no object of the worker shows the database
password, and the migration files pass the checks a deploy runs.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

from ekbatan.config import split_login
from ekbatan.migrations import validate_scripts

from .actions import WalletCreateAction
from .app import CONFIG_FILE, MIGRATIONS, Worker, build
from .model import NotificationKind, Wallet

GLOBAL_SHARD = WalletCreateAction.GLOBAL_SHARD
MEXICO_SHARD = WalletCreateAction.MEXICO_SHARD
#: The containers' password: a marker no object of the worker may show.
SECRET = "Mk-7f3a-SECRET"


@pytest.fixture(scope="module")
def databases() -> Iterator[dict[str, str]]:
    """Two servers, one per shard, and what fills `ekbatan.yaml`'s placeholders from them."""
    with (
        PostgresContainer(
            "postgres:17-alpine", driver="psycopg", password=SECRET, dbname="wallet_global"
        ) as global_server,
        PostgresContainer(
            "postgres:17-alpine", driver="psycopg", password=SECRET, dbname="wallet_mexico"
        ) as mexico_server,
    ):
        global_url, username, password = split_login(global_server.get_connection_url())
        mexico_url, _, _ = split_login(mexico_server.get_connection_url())
        yield {
            "WALLET_GLOBAL_URL": global_url,
            "WALLET_MEXICO_URL": mexico_url,
            "WALLET_DATABASE_USERNAME": username or "",
            "WALLET_DATABASE_PASSWORD": password or "",
        }


def use(monkeypatch: pytest.MonkeyPatch, databases: dict[str, str]) -> None:
    for name, value in databases.items():
        monkeypatch.setenv(name, value)


def fast_config(directory: Path) -> Path:
    """`ekbatan.yaml` with the polling tightened, as Java's test sets durations of its own, so
    the chain converges in a second rather than in ten."""
    text_ = CONFIG_FILE.read_text().replace("pollingInterval: PT10S", "pollingInterval: PT0.2S")
    text_ = text_.replace(
        "  local-event-handler:\n",
        "  local-event-handler:\n    fanoutPollDelay: PT0.2S\n    handlingPollDelay: PT0.2S\n",
    )
    path = directory / "ekbatan.yaml"
    path.write_text(text_)
    return path


@pytest.fixture
def worker(
    databases: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Worker]:
    use(monkeypatch, databases)
    built = build(config_file=fast_config(tmp_path))
    built.start()
    try:
        yield built
    finally:
        built.close()


def eventually(check: Callable[[], bool], within: float = 20.0) -> bool:
    """Polled: the stipend job, fan-out and dispatch are real jobs on a real schedule."""
    deadline = time.monotonic() + within
    while not check():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.3)
    return True


def a_wallet(worker: Worker, country_code: str, currency: str) -> Wallet:
    return worker.executor.execute(
        WalletCreateAction,
        WalletCreateAction.Params(
            country_code=country_code,
            owner_id=uuid4(),
            currency=currency,
            initial_balance=Decimal("0"),
        ),
        principal="test-user",
    )


def test_stipend_job_tops_up_underfunded_wallets_on_each_shard_and_triggers_notifications(
    worker: Worker,
) -> None:
    global_wallet = a_wallet(worker, "US", "USD")
    mexico_wallet = a_wallet(worker, "MX", "MXN")

    wallet_repository = worker.wallet_repository
    assert wallet_repository.exists_on_shard(GLOBAL_SHARD, global_wallet.id.value)
    assert not wallet_repository.exists_on_shard(MEXICO_SHARD, global_wallet.id.value)
    assert wallet_repository.exists_on_shard(MEXICO_SHARD, mexico_wallet.id.value)
    assert not wallet_repository.exists_on_shard(GLOBAL_SHARD, mexico_wallet.id.value)

    def topped_up_and_notified() -> bool:
        for wallet in (global_wallet, mexico_wallet):
            if wallet_repository.get_by_id(wallet.id.value).balance < Decimal("10.00"):
                return False
            notifications = worker.notification_repository.find_all_by_wallet_id(wallet.id.value)
            if not notifications:
                return False
            assert all(n.kind is NotificationKind.MONEY_DEPOSITED for n in notifications)
        return True

    assert eventually(topped_up_and_notified), "the stipend never reached both wallets"


def test_driver_settings_from_the_configuration_reach_the_database(worker: Worker) -> None:
    """`ekbatan.yaml` gives every primaryConfig a dataSourceProperties entry; each shard's
    primary pool must have handed it to the driver."""
    for shard in (GLOBAL_SHARD, MEXICO_SHARD):
        with worker.database_registry.manager(shard).connection() as connection:
            name = connection.execute(text("SELECT current_setting('application_name')"))
            assert name.scalar() == "wallet-service", shard


def test_no_worker_object_shows_the_password(
    databases: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The containers' password is the marker `SECRET`. The worker is built from the
    environment and really connects with it - and not one object it holds shows it."""
    use(monkeypatch, databases)
    built = build()
    try:
        with built.database_registry.default.connection() as connection:
            assert connection.execute(text("SELECT 1")).scalar() == 1, "connected with it"
        sources = [
            *(
                built.database_registry.manager(shard).primary
                for shard in built.database_registry.shards
            ),
            built.lock_data_source,
            built.jobs_data_source,
        ]
        shown = [
            built,
            built.database_registry,
            built.executor,
            built.lock_provider,
            built.job_registry,
            *sources,
            *(source.sqlalchemy_engine for source in sources),
            *(source.sqlalchemy_engine.pool for source in sources),
        ]
        for thing in shown:
            assert SECRET not in repr(thing), type(thing).__name__
            assert SECRET not in str(thing), type(thing).__name__
    finally:
        built.close()


def test_the_migration_files_pass_the_checks_a_deploy_runs() -> None:
    """No database needed, so CI catches it: two files with one version, a file whose name would
    be skipped without a word, a rollback file. Each is refused here rather than at startup."""
    assert [script.script for script in validate_scripts(MIGRATIONS)] == [
        "V0001__eventlog.sql",
        "V0002__scheduled_tasks.sql",
        "V0003__wallets.sql",
        "V0004__notifications.sql",
    ]
