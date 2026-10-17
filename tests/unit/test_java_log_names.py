"""Log and trace names are the Java implementation's, so one logging config covers both ports.

Java names each logger after its class - `LoggerFactory.getLogger(ActionExecutor.class)` - and
an operator's config says `io.ekbatan.core.action: DEBUG`. Each Python logger below is named
after the Java class that writes the same lines; every name was checked against a class file
in the Java port when it was set.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from ekbatan import _tracing, migrations
from ekbatan.action import executor, persister
from ekbatan.config import DataSourceConfig
from ekbatan.distributedjobs import _shutdown
from ekbatan.events import dispatch, fanout
from ekbatan.locks import _deadlines, _holder, sql
from ekbatan.migrations import MDC_SHARD, MDC_TARGET, Target
from ekbatan.sharding import ShardIdentifier

SRC = Path(__file__).resolve().parents[2] / "src" / "ekbatan"


@pytest.mark.parametrize(
    ("logger", "java"),
    [
        (executor.LOG, "io.ekbatan.core.action.ActionExecutor"),
        (executor.RETRY_LOG, "io.ekbatan.core.action.Retry"),
        (persister.LOG, "io.ekbatan.core.action.persister.ChangePersister"),
        (_holder.LOG, "io.ekbatan.core.concurrent.KeyedReentrantHolder"),
        (_deadlines.LOG, "io.ekbatan.core.concurrent.KeyedReentrantHolder"),
        (
            sql.PostgresKeyedLockProvider._log,
            "io.ekbatan.core.concurrent.PostgresKeyedLockProvider",
        ),
        (sql.MySQLKeyedLockProvider._log, "io.ekbatan.core.concurrent.MySQLKeyedLockProvider"),
        (sql.MariaDBKeyedLockProvider._log, "io.ekbatan.core.concurrent.MariaDBKeyedLockProvider"),
        (_shutdown.LOG, "io.ekbatan.distributedjobs.JobRegistry"),
        (fanout.LOG, "io.ekbatan.events.localeventhandler.job.EventFanoutJob"),
        (dispatch.LOG, "io.ekbatan.events.localeventhandler.job.EventHandlingJob"),
        (migrations.LOG, "io.ekbatan.flyway.FlywayMigrator"),
    ],
)
def test_a_logger_is_named_after_its_java_class(logger: logging.Logger, java: str) -> None:
    assert logger.name == java


def test_the_other_modules_too() -> None:
    """The modules whose import pulls in optional packages, checked through their source."""
    for path, java in [
        ("persistence.py", "io.ekbatan.core.persistence.TransactionManager"),
        ("persistence.py", "io.ekbatan.core.shard.DatabaseRegistry"),
        ("_migration_backend.py", "io.ekbatan.flyway.FlywayMigrator"),
        ("locks/redis.py", "io.ekbatan.keyedlock.redis.RedisKeyedLockProvider"),
        ("distributedjobs/__init__.py", "io.ekbatan.distributedjobs.JobRegistry"),
    ]:
        assert f'logging.getLogger("{java}")' in (SRC / path).read_text(), (path, java)


#: The Flyway 12 classes whose lines the migrator writes, as Flyway writes them - each name
#: checked against the class in flyway-core 12.0.0, so `org.flywaydb: WARN` quietens both ports.
FLYWAY_CLASSES = {
    "org.flywaydb.core.internal.command.DbMigrate",
    "org.flywaydb.core.internal.command.DbValidate",
    "org.flywaydb.core.internal.command.DbBaseline",
    "org.flywaydb.core.internal.command.DbRepair",
    "org.flywaydb.core.internal.schemahistory.JdbcTableSchemaHistory",
    "org.flywaydb.core.internal.sqlscript.DefaultSqlScriptExecutor",
    "org.flywaydb.core.internal.resource.ResourceNameValidator",
    "org.flywaydb.core.internal.callback.SqlScriptCallbackFactory",
    "org.flywaydb.core.internal.scanner.filesystem.FileSystemScanner",
}


def test_every_logger_is_under_io_ekbatan_or_is_flyways_own() -> None:
    """So `io.ekbatan: WARN` - the one line a Java config has - silences or opens all of them,
    and the migrator's Flyway lines answer to `org.flywaydb`, as they do in Java."""
    names = {
        name
        for path in SRC.rglob("*.py")
        for name in re.findall(r'logging\.getLogger\("([^"]+)"\)', path.read_text())
    }
    assert names, "the scan found no loggers at all"
    assert {n for n in names if not n.startswith("io.ekbatan.")} <= FLYWAY_CLASSES


def test_the_tracer_scope_is_javas() -> None:
    assert _tracing.SCOPE == "io.ekbatan.core"


class TestMigrationLogContext:
    """Java's `FlywayMigrator.inLogContext`: the shard and target name on every line logged while
    a target migrates - the MDC there, record attributes here."""

    @staticmethod
    def target(name: str, group: int, member: int) -> Target:
        source = DataSourceConfig.builder().url("sqlite://").build()
        return migrations.target(source, shard=ShardIdentifier(group, member), name=name)

    def test_the_keys_are_javas(self) -> None:
        assert (MDC_SHARD, MDC_TARGET) == ("ekbatanShard", "ekbatanTarget")

    def test_a_record_logged_inside_carries_both(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            caplog.at_level(logging.INFO, logger=migrations.LOG.name),
            migrations._in_log_context(self.target("mexico/member-0", 1, 0)),
        ):
            migrations.LOG.info("inside")
        (record,) = caplog.records
        assert record.ekbatanShard == "1:0"
        assert record.ekbatanTarget == "mexico/member-0"

    def test_a_record_logged_outside_has_neither(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger=migrations.LOG.name):
            migrations.LOG.info("outside")
        (record,) = caplog.records
        assert not hasattr(record, MDC_SHARD)
        assert not hasattr(record, MDC_TARGET)

    def test_what_was_there_before_is_put_back(self, caplog: pytest.LogCaptureFixture) -> None:
        """Java restores the caller's values, success or failure; so does this."""
        with caplog.at_level(logging.INFO, logger=migrations.LOG.name):
            with migrations._in_log_context(self.target("outer", 0, 0)):
                with (
                    pytest.raises(RuntimeError),
                    migrations._in_log_context(self.target("inner", 2, 3)),
                ):
                    raise RuntimeError("the migration failed")
                migrations.LOG.info("after")
            migrations.LOG.info("outside")
        after, outside = caplog.records
        assert (after.ekbatanShard, after.ekbatanTarget) == ("0:0", "outer")
        assert not hasattr(outside, MDC_TARGET)

    def test_a_java_style_format_works(self) -> None:
        """`%X{ekbatanShard}` in Java's pattern is `%(ekbatanShard)s` here; `defaults` covers the
        lines logged outside a migration, where Java's MDC prints nothing."""
        formatter = logging.Formatter(
            "[%(ekbatanShard)s %(ekbatanTarget)s] %(message)s",
            defaults={MDC_SHARD: "", MDC_TARGET: ""},
        )
        record = logging.LogRecord(migrations.LOG.name, logging.INFO, "", 0, "plain", None, None)
        assert formatter.format(record) == "[ ] plain"
