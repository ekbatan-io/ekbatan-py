"""The repository contract, run against whichever dialects the run asked for."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repository_contract import LockAndFetchContract, RepositoryContract


class TestRepository(RepositoryContract):
    """Every dialect must pass all of it."""


class TestLockAndFetch(LockAndFetchContract):
    """Where the dialect can claim in the statement that selects."""
