"""The suite's migration files are exactly what `fixtures.schema` generates.

They are committed, because a deployment migrates from committed files and the suite should too.
They are generated, because the table shapes are a contract and hand-copied DDL drifts. This is
what holds the two together: change the metadata without regenerating and it fails.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from ekbatan.migrations import validate_scripts

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _generator():
    spec = importlib.util.spec_from_file_location("generate", MIGRATIONS / "generate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATE = _generator()


@pytest.mark.parametrize("directory", sorted(set(GENERATE.DIRECTORIES.values())))
def test_the_committed_file_is_what_the_metadata_generates(directory):
    committed = (MIGRATIONS / directory / GENERATE.FILE).read_text()

    assert committed == GENERATE.render(directory), (
        f"tests/migrations/{directory}/{GENERATE.FILE} is stale: "
        f"run `uv run python tests/migrations/generate.py`"
    )


@pytest.mark.parametrize("directory", sorted(set(GENERATE.DIRECTORIES.values())))
def test_the_suite_s_migrations_pass_the_checks_a_deployment_s_would(directory):
    assert [s.script for s in validate_scripts(MIGRATIONS / directory)] == [GENERATE.FILE]
