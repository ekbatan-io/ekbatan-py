"""Alembic, driven as a library, across every shard - plus the guard Alembic does not have.

Java runs Flyway here. The two tools agree on ordering, once-only application and transactional
DDL; they disagree on whether editing an applied migration is noticed. Flyway refuses it, Alembic
accepts it silently, and `ekbatan.migrations` puts that back.
"""

from __future__ import annotations

import pytest
from sqlalchemy import MetaData, create_engine, inspect, select

from ekbatan.config import DataSourceConfig, ShardingConfig
from ekbatan.errors import ValidationError
from ekbatan.migrations import CHECKSUM_TABLE, MigrationError, checksum_table, migrate
from ekbatan.sharding import ShardIdentifier
from fixtures.config import group, member

pytest.importorskip("alembic")

GLOBAL = ShardIdentifier(0, 0)
MEXICO = ShardIdentifier(1, 0)

ENV = """
from alembic import context
from sqlalchemy import create_engine

engine = create_engine(context.config.get_main_option("sqlalchemy.url"))
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
"""

REVISION = '''
"""{message}"""
from alembic import op

revision = "{revision}"
down_revision = {down!r}


def upgrade():
    op.execute("""{sql}""")


def downgrade():
    pass
'''


class Scripts:
    """A real Alembic script directory, built without `alembic init`."""

    def __init__(self, path):
        self.path = path
        (path / "versions").mkdir(parents=True)
        (path / "env.py").write_text(ENV)
        (path / "script.py.mako").write_text('"""${message}"""\n')

    def revision(self, name: str, revision_id: str, down: str | None, sql: str) -> None:
        (self.path / "versions" / f"{revision_id}_{name}.py").write_text(
            REVISION.format(message=name, revision=revision_id, down=down, sql=sql)
        )

    def drop(self, filename: str) -> None:
        (self.path / "versions" / filename).unlink()


@pytest.fixture
def scripts(tmp_path):
    made = Scripts(tmp_path / "migrations")
    made.revision("widgets", "0001", None, "CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
    made.revision("gadgets", "0002", "0001", "CREATE TABLE gadgets (id INTEGER PRIMARY KEY)")
    return made


@pytest.fixture
def one_shard(backend, request):
    name = "ekb_mig_" + request.node.name.replace("[", "_").replace("]", "").lower()[:36]
    url = backend.url(name)
    return ShardingConfig.single(url), url


def test_alembic_runs_as_a_library_with_no_ini_and_no_binary(one_shard, scripts):
    """The whole reason for choosing it: `migrate` is a function call, not a subprocess."""
    config, url = one_shard

    at = migrate(config, scripts.path)

    assert at == {GLOBAL: "0002"}
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {"widgets", "gadgets", "alembic_version", CHECKSUM_TABLE} <= tables
    finally:
        engine.dispose()


def test_running_again_changes_nothing(one_shard, scripts):
    config, _ = one_shard
    migrate(config, scripts.path)

    assert migrate(config, scripts.path) == {GLOBAL: "0002"}


def test_a_new_revision_is_picked_up(one_shard, scripts):
    config, url = one_shard
    migrate(config, scripts.path)

    scripts.revision("sprockets", "0003", "0002", "CREATE TABLE sprockets (id INTEGER PRIMARY KEY)")

    assert migrate(config, scripts.path) == {GLOBAL: "0003"}
    engine = create_engine(url)
    try:
        assert "sprockets" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_editing_an_applied_revision_is_refused(one_shard, scripts):
    """The guard Alembic does not have.

    Measured before this existed: Alembic upgraded again without complaint, and the database
    kept the old columns while the file described new ones. Flyway refuses; so does this.
    """
    config, _ = one_shard
    migrate(config, scripts.path)

    scripts.revision(
        "widgets", "0001", None, "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT)"
    )

    with pytest.raises(MigrationError, match="has changed since it was applied"):
        migrate(config, scripts.path)


def test_deleting_an_applied_revision_is_refused(one_shard, scripts):
    config, _ = one_shard
    migrate(config, scripts.path)

    scripts.drop("0002_gadgets.py")

    with pytest.raises(MigrationError, match="applied to this database but is not in"):
        migrate(config, scripts.path)


def test_what_was_applied_is_recorded_per_revision(one_shard, scripts):
    """Alembic's `alembic_version` holds one row - the current head. This holds one per
    revision, which is what makes the checksum check possible at all."""
    config, url = one_shard
    migrate(config, scripts.path)

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            rows = list(conn.execute(select(checksum_table(MetaData())).order_by("revision")))
    finally:
        engine.dispose()

    assert [r.revision for r in rows] == ["0001", "0002"]
    assert [r.script for r in rows] == ["0001_widgets.py", "0002_gadgets.py"]
    assert all(len(r.checksum) == 64 for r in rows), "sha256 of the file"


def test_a_missing_script_directory_says_so(one_shard, tmp_path):
    config, _ = one_shard

    with pytest.raises(ValidationError, match="no Alembic script directory"):
        migrate(config, tmp_path / "nowhere")


def test_a_failure_names_the_shard_and_what_had_already_been_done(backend, scripts, request):
    """One shard at a time, fail-fast. The underlying error names a connection url; an operator
    needs to know how far the run got before deciding whether to re-run it."""
    scripts.revision("broken", "0003", "0002", "CREATE TABLE ( THIS IS NOT SQL")
    name = "ekb_mig_fail_" + request.node.name.replace("[", "_").replace("]", "").lower()[:24]
    config = ShardingConfig(
        default_shard=GLOBAL,
        groups=(
            group(0, "global", member(0, DataSourceConfig(url=backend.url(name + "_a")))),
            group(1, "mexico", member(0, DataSourceConfig(url=backend.url(name + "_b")))),
        ),
    )

    with pytest.raises(MigrationError) as raised:
        migrate(config, scripts.path)

    message = str(raised.value)
    assert "0:0" in message, "the shard that failed"
    assert "Already migrated: none" in message
    assert "the applied ones are skipped" in message, "and what to do about it"


# Java's `FlywayMigrator.migrate(Target...)`: a named subset of the layout, for rolling a
# migration out one group at a time rather than to every database at once.


def _two_shards(backend, request, suffix=""):
    name = "ekb_mig_sub_" + request.node.name.replace("[", "_").replace("]", "").lower()[:20]
    return ShardingConfig(
        default_shard=GLOBAL,
        groups=(
            group(0, "global", member(0, DataSourceConfig(url=backend.url(name + suffix + "_a")))),
            group(1, "mexico", member(0, DataSourceConfig(url=backend.url(name + suffix + "_b")))),
        ),
    )


def test_a_named_subset_migrates_only_those_shards(backend, scripts, request):
    config = _two_shards(backend, request)

    at = migrate(config, scripts.path, shards=[MEXICO])

    assert at == {MEXICO: "0002"}, "only the shard that was asked for"

    untouched = create_engine(config.member(GLOBAL).primary_config().url)
    try:
        assert "widgets" not in set(inspect(untouched).get_table_names()), (
            "the shard nobody named must not have been migrated"
        )
    finally:
        untouched.dispose()


def test_and_the_rest_can_be_migrated_afterwards(backend, scripts, request):
    """The point of a subset: the remainder is still migratable, and the first group is not
    re-run from scratch when it is."""
    config = _two_shards(backend, request)

    migrate(config, scripts.path, shards=[MEXICO])
    at = migrate(config, scripts.path)

    assert at == {GLOBAL: "0002", MEXICO: "0002"}, "both, and the already-done one is a no-op"


def test_shards_are_migrated_in_layout_order_not_argument_order(backend, scripts, request):
    """So a run that stops halfway leaves a state the operator can reason about."""
    config = _two_shards(backend, request)

    at = migrate(config, scripts.path, shards=[MEXICO, GLOBAL])

    assert list(at) == [GLOBAL, MEXICO]


def test_a_shard_the_layout_does_not_have_is_refused(backend, scripts, request):
    """Rather than skipped. A typo would otherwise read as "nothing to do" and migrate nothing,
    which is the failure this whole module exists to prevent."""
    config = _two_shards(backend, request)

    with pytest.raises(ValidationError) as raised:
        migrate(config, scripts.path, shards=[ShardIdentifier(9, 9)])

    assert "9:9" in str(raised.value)
    assert "not in this layout" in str(raised.value)


def test_an_empty_subset_migrates_nothing_and_says_so(backend, scripts, request):
    """Distinct from `shards=None`, which means every shard. Passing an empty collection is a
    caller that computed a subset and found it empty; doing the whole layout instead would be
    the opposite of what they asked."""
    config = _two_shards(backend, request)

    assert migrate(config, scripts.path, shards=[]) == {}
