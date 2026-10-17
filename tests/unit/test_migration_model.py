"""The files against a database's history, as the pure function it is.

`_model` is what migrate, info and repair all work from: what is pending, what is refused, what is
only warned about, and where every file and every row stands. Each rule is Flyway's, run against
Flyway 12 before it was written here - the parity scenario named in each test is the run, in
`scripts/flyway-parity.py`, and `docs/MIGRATOR-LEDGER.md` records what it showed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ekbatan.migrations import (
    MigrationError,
    MigrationScript,
    MigrationState,
    Scripts,
    _model,
    _Row,
    _scan,
)


def files(tmp_path: Path, *spec: str | tuple[str, str]) -> Scripts:
    """Migration files, each with its own content unless one is given."""
    for item in spec:
        name, sql = item if isinstance(item, tuple) else (item, f"SELECT '{item}';")
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sql)
    return _scan(tmp_path)


def script(scripts: Scripts, name: str) -> MigrationScript:
    (found,) = [s for s in scripts.migrations if s.script == name]
    return found


def applied(
    found: MigrationScript,
    rank: int,
    *,
    success: bool = True,
    checksum: str | None = None,
    description: str | None = None,
    name: str | None = None,
) -> _Row:
    return _Row(
        installed_rank=rank,
        version=found.version,
        description=found.description if description is None else description,
        type="SQL",
        script=name or found.script,
        checksum=found.checksum if checksum is None else checksum,
        installed_by="me",
        installed_on="2026-10-01T00:00:00.000000Z",
        execution_time=1,
        success=success,
    )


def gone(version: str | None, description: str, rank: int, *, success: bool = True) -> _Row:
    """A row whose file is not among the files."""
    script = f"V{version}__{description}.sql" if version else f"R__{description}.sql"
    return _Row(rank, version, description, "SQL", script, "0" * 64, "me", "t", 1, success)


def baseline(version: str, rank: int = 1) -> _Row:
    return _Row(
        rank, version, "<< Baseline >>", "BASELINE", "<< Baseline >>", None, "me", "t", 0, True
    )


def deleted(row: _Row, rank: int) -> _Row:
    return row._replace(installed_rank=rank, type="DELETE", execution_time=0)


def pending(model) -> list[str]:
    return [s.script for s in model.pending]


def states(model) -> list[tuple[str | None, str, str]]:
    return [(i.version, i.description, i.state.value) for i in model.infos]


# -- versioned -------------------------------------------------------------------------------


def test_nothing_applied_means_everything_is_pending_versioned_first(tmp_path):
    scripts = files(tmp_path, "R__view.sql", "V2__b.sql", "V1__a.sql")

    model = _model(scripts, [])

    assert pending(model) == ["V1__a.sql", "V2__b.sql", "R__view.sql"]
    assert model.problems == [] and model.warnings == []
    assert model.schema_version is None


def test_only_what_has_not_run_is_pending(tmp_path):
    scripts = files(tmp_path, "V1__a.sql", "V2__b.sql", "V3__c.sql")
    one, two = script(scripts, "V1__a.sql"), script(scripts, "V2__b.sql")

    model = _model(scripts, [applied(one, 1), applied(two, 2)])

    assert pending(model) == ["V3__c.sql"]
    assert model.schema_version == "2"


def test_an_edited_migration_is_refused_and_can_be_realigned(tmp_path):
    """Flyway: "Migration checksum mismatch" (parity X04a)."""
    scripts = files(tmp_path, "V1__a.sql")
    one = script(scripts, "V1__a.sql")
    history = [applied(one, 1, checksum="0" * 64)]

    model = _model(scripts, history)

    with pytest.raises(MigrationError, match="has changed since it was applied"):
        model.refuse("shard")
    assert model.misaligned == [(history[0], one)], "what repair would realign"


def test_a_new_description_is_refused_and_can_be_realigned(tmp_path):
    """Flyway: "Migration description mismatch" (parity X04c)."""
    scripts = files(tmp_path, "V1__deux.sql")
    one = script(scripts, "V1__deux.sql")
    history = [applied(one, 1, description="two", name="V1__two.sql")]

    model = _model(scripts, history)

    with pytest.raises(MigrationError, match=r"was applied as 'two'.*now says 'deux'"):
        model.refuse("shard")
    assert model.misaligned == [(history[0], one)]


@pytest.mark.parametrize(
    ("then", "now"),
    [("V2__two.sql", "V002__two.sql"), ("V2__two.sql", "sub/V2__two.sql")],
    ids=["version respelled", "moved into a subdirectory"],
)
def test_a_new_file_name_with_the_same_version_and_description_is_not_a_change(tmp_path, then, now):
    """Flyway compares the version, the description and the checksum, not the path: zero-padding
    every file name, or sorting them into directories, is not refused (parity M12b, M12c)."""
    scripts = files(tmp_path, (now, "SELECT 2;"))
    moved = script(scripts, now)

    model = _model(scripts, [applied(moved, 1, name=then)])

    assert model.problems == [] and pending(model) == []


def test_an_older_applied_migration_whose_file_is_gone_is_refused(tmp_path):
    """Flyway: "Detected applied migration not resolved locally" (parity X04b)."""
    scripts = files(tmp_path, "V2__b.sql")
    two = script(scripts, "V2__b.sql")
    missing = gone("1", "a", 1)

    model = _model(scripts, [missing, applied(two, 2)])

    with pytest.raises(MigrationError, match=r"V1__a\.sql \(version 1\).*its file is gone"):
        model.refuse("shard")
    assert model.missing == [missing], "what repair would mark deleted"


def test_an_applied_migration_newer_than_every_file_is_left_alone_with_a_warning(tmp_path):
    """Flyway's default: an older release starting against a database a newer one migrated.
    Refusing would stop a rollback of the application from starting at all (parity R16)."""
    scripts = files(tmp_path, "V1__a.sql")
    one = script(scripts, "V1__a.sql")

    model = _model(scripts, [applied(one, 1), gone("2", "b", 2)])

    assert model.problems == [] and pending(model) == []
    assert len(model.warnings) == 1 and "newer than every migration here" in model.warnings[0]
    assert model.schema_version == "2", "the database is at the newer version"
    assert states(model)[-1] == ("2", "b", "Future")


def test_a_new_migration_older_than_one_already_applied_is_refused(tmp_path):
    """Flyway: "Detected resolved migration not applied to database" (outOfOrder is off)."""
    scripts = files(tmp_path, "V1__a.sql", "V2__b.sql", "V3__c.sql")
    one, three = script(scripts, "V1__a.sql"), script(scripts, "V3__c.sql")

    model = _model(scripts, [applied(one, 1), applied(three, 2)])

    with pytest.raises(MigrationError) as raised:
        model.refuse("shard")
    assert "V2__b.sql has not been applied, but version 3 already has" in str(raised.value)
    assert "nothing was run" in str(raised.value)
    assert ("2", "b", "Ignored") in states(model)


def test_a_failed_migration_is_refused_until_repaired(tmp_path):
    """Flyway: "Detected failed migration to version 2 (half). Please remove any half-completed
    changes then run repair" - even once the file is fixed (parity X02)."""
    scripts = files(tmp_path, "V1__a.sql", "V2__half.sql")
    one, half = script(scripts, "V1__a.sql"), script(scripts, "V2__half.sql")

    model = _model(scripts, [applied(one, 1), applied(half, 2, success=False, checksum="0" * 64)])

    with pytest.raises(MigrationError, match=r"version 2 \(V2__half\.sql\) failed on an earlier"):
        model.refuse("shard")
    assert pending(model) == [], "never applied again over what it half did"
    assert model.schema_version == "2", "a failed row still counts: the database was touched"
    assert states(model)[-1] == ("2", "half", "Failed")


def test_a_failed_migration_a_newer_release_recorded_is_only_a_warning(tmp_path):
    """Flyway: "contains a failed future migration" - and it carries on (parity X02c)."""
    scripts = files(tmp_path, "V1__a.sql")
    one = script(scripts, "V1__a.sql")

    model = _model(scripts, [applied(one, 1), gone("2", "half", 2, success=False)])

    assert model.problems == []
    assert "failed when a newer release applied it" in model.warnings[0]
    assert states(model)[-1] == ("2", "half", "Failed (Future)")


def test_a_failed_migration_whose_file_is_gone_is_refused_with_its_own_advice(tmp_path):
    """No file to fix and run again: only the database to check, and the row to repair. (With no
    file newer than it, it would be a newer release's failure instead - a warning.)"""
    scripts = files(tmp_path, "V1__a.sql", "V3__c.sql")
    one = script(scripts, "V1__a.sql")

    model = _model(scripts, [applied(one, 1), gone("2", "half", 2, success=False)])

    with pytest.raises(MigrationError, match=r"failed on an earlier run, and its file is gone"):
        model.refuse("shard")
    assert ("2", "half", "Failed (Missing)") in states(model)


def test_every_problem_is_reported_at_once(tmp_path):
    scripts = files(tmp_path, "V1__a.sql", "V2__b.sql", "V3__c.sql")
    one, three = script(scripts, "V1__a.sql"), script(scripts, "V3__c.sql")

    model = _model(scripts, [applied(one, 1, checksum="0" * 64), applied(three, 2)])

    with pytest.raises(MigrationError) as raised:
        model.refuse("wallet (0:0)")
    message = str(raised.value)
    assert message.startswith("wallet (0:0):"), "the database it is about"
    assert "V1__a.sql has changed" in message
    assert "V2__b.sql has not been applied" in message


# -- repeatable ------------------------------------------------------------------------------


def test_a_repeatable_migration_runs_once_while_its_file_is_unchanged(tmp_path):
    """Parity R03."""
    scripts = files(tmp_path, "R__x.sql")
    x = script(scripts, "R__x.sql")

    assert pending(_model(scripts, [])) == ["R__x.sql"]
    assert pending(_model(scripts, [applied(x, 1)])) == []


def test_a_changed_repeatable_migration_runs_again_and_the_old_row_is_superseded(tmp_path):
    """Parity R04: a new row each time; the older one shows as superseded, the newer as
    outdated until it has been applied again."""
    scripts = files(tmp_path, ("R__x.sql", "SELECT 'x2';"))
    x = script(scripts, "R__x.sql")
    history = [applied(x, 1, checksum="1" * 64), applied(x, 2, checksum="2" * 64)]

    model = _model(scripts, history)

    assert pending(model) == ["R__x.sql"]
    assert model.problems == []
    assert states(model) == [
        (None, "x", "Superseded"),
        (None, "x", "Outdated"),
        (None, "x", "Pending"),
    ]


def test_only_the_newest_row_counts_so_a_file_changed_back_runs_again(tmp_path):
    """A, then B, then A again: three applications (parity R06)."""
    scripts = files(tmp_path, ("R__x.sql", "SELECT 'A';"))
    a = script(scripts, "R__x.sql")

    model = _model(scripts, [applied(a, 1), applied(a, 2, checksum="b" * 64)])

    assert pending(model) == ["R__x.sql"]


def test_only_the_changed_repeatable_runs(tmp_path):
    """Parity R14."""
    scripts = files(tmp_path, "R__a.sql", "R__b.sql")
    a, b = script(scripts, "R__a.sql"), script(scripts, "R__b.sql")

    model = _model(scripts, [applied(a, 1), applied(b, 2, checksum="0" * 64)])

    assert pending(model) == ["R__b.sql"]


def test_repeatables_are_pending_in_description_order_after_versioned_ones(tmp_path):
    # `C` rather than `B`: beside `R__b.sql`, `R__B.sql` is the same file on a macOS disk.
    scripts = files(tmp_path, "R__b.sql", "R__C.sql", "V3__c.sql", "R__a.sql")

    assert pending(_model(scripts, [])) == ["V3__c.sql", "R__C.sql", "R__a.sql", "R__b.sql"]


def test_a_repeatable_whose_file_is_gone_is_refused(tmp_path):
    """Flyway: "Detected applied migration not resolved locally: x. If you removed this migration
    intentionally, run repair to mark the migration as deleted." (parity R07). A rename is the
    same, with the new description pending (parity R08)."""
    scripts = files(tmp_path, "R__y.sql")
    x = gone(None, "x", 1)

    model = _model(scripts, [x])

    with pytest.raises(MigrationError, match=r"repeatable migration 'x'.*its file is gone"):
        model.refuse("shard")
    assert model.missing == [x]
    assert pending(model) == ["R__y.sql"]


def test_a_repeatable_a_newer_release_added_stops_an_older_one(tmp_path):
    """Flyway has no "future" for a repeatable: an older release refuses to start against a
    database a newer one added a repeatable to (parity R23). The same here, and said."""
    scripts = files(tmp_path, "V1__a.sql")
    one = script(scripts, "V1__a.sql")

    model = _model(scripts, [applied(one, 1), gone("2", "b", 2), gone(None, "new", 3)])

    with pytest.raises(MigrationError, match="An older release started against a database"):
        model.refuse("shard")


def test_a_repeatable_marked_deleted_is_no_longer_missing_and_runs_again_if_it_returns(tmp_path):
    """Parity R24: repair writes a DELETE row; after it, nothing is refused - and a file of that
    description coming back is applied again."""
    x_row = gone(None, "x", 1)
    history = [x_row, deleted(x_row, 2)]

    without = _model(files(tmp_path / "a", "V1__a.sql"), history)
    assert without.problems == [] and without.missing == []
    assert (None, "x", "Deleted") in states(without)

    back = _model(files(tmp_path / "b", ("R__x.sql", "SELECT 'x';")), history)
    assert pending(back) == ["R__x.sql"]


def test_a_failed_repeatable_is_refused(tmp_path):
    """Flyway: "Detected failed repeatable migration: x" (parity R10)."""
    scripts = files(tmp_path, "R__x.sql")
    x = script(scripts, "R__x.sql")

    model = _model(scripts, [applied(x, 1, success=False)])

    with pytest.raises(MigrationError, match=r"repeatable migration 'x' \(R__x\.sql\) failed"):
        model.refuse("shard")


def test_a_repeatable_row_does_not_count_as_a_version(tmp_path):
    scripts = files(tmp_path, "R__x.sql")
    x = script(scripts, "R__x.sql")

    assert _model(scripts, [applied(x, 1)]).schema_version is None


# -- baseline and deleted rows ---------------------------------------------------------------


def test_what_a_baseline_covers_never_runs_and_what_follows_does(tmp_path):
    """Parity X05d: at version 2, V1 is below the baseline, V2 is the baseline, V3 runs."""
    scripts = files(tmp_path, "V1__a.sql", "V2__b.sql", "V3__c.sql")

    model = _model(scripts, [baseline("2")])

    assert pending(model) == ["V3__c.sql"]
    assert model.problems == []
    assert model.schema_version == "2"
    assert states(model) == [
        ("1", "a", "Below Baseline"),
        ("2", "b", "Ignored (Baseline)"),
        ("2", "<< Baseline >>", "Baseline"),
        ("3", "c", "Pending"),
    ]


def test_a_baseline_row_is_not_missing_a_file(tmp_path):
    scripts = files(tmp_path, "V3__c.sql")

    assert _model(scripts, [baseline("2")]).problems == []


def test_a_versioned_migration_marked_deleted_that_comes_back_is_out_of_order(tmp_path):
    """Parity X04b: once repair marks V2 deleted, nothing is refused. The database is at V3, so a
    V2 file that comes back is older than what has been applied, and refused as such - as Flyway
    refuses it."""
    without = files(tmp_path / "a", "V1__a.sql", "V3__c.sql")
    one, three = script(without, "V1__a.sql"), script(without, "V3__c.sql")
    two = gone("2", "b", 2)
    history = [applied(one, 1), two, applied(three, 3), deleted(two, 4)]

    model = _model(without, history)
    assert model.problems == [] and pending(model) == []
    assert ("2", "b", "Deleted") in states(model)

    back = _model(files(tmp_path / "b", "V1__a.sql", "V2__b.sql", "V3__c.sql"), history)
    with pytest.raises(MigrationError, match=r"V2__b\.sql has not been applied, but version 3"):
        back.refuse("shard")


def test_info_lists_what_a_baseline_skipped_then_the_history_then_what_is_pending(tmp_path):
    scripts = files(tmp_path, "V1__a.sql", "V2__b.sql", "R__v.sql", "R__u.sql")
    two = script(scripts, "V2__b.sql")
    v = script(scripts, "R__v.sql")

    model = _model(scripts, [baseline("1"), applied(v, 2), applied(two, 3)])

    assert [(i.category, i.description, i.state) for i in model.infos] == [
        ("Versioned", "a", MigrationState.BASELINE_IGNORED),
        ("", "<< Baseline >>", MigrationState.BASELINE),
        ("Repeatable", "v", MigrationState.SUCCESS),
        ("Versioned", "b", MigrationState.SUCCESS),
        ("Repeatable", "u", MigrationState.PENDING),
    ]
