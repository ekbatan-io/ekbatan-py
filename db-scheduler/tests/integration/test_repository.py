"""The repository contract, run against whichever dialects the run asked for."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repository_contract import (
    BatchAndPagingContract,
    LockAndFetchContract,
    PagingContract,
    RepositoryContract,
    ResolverContract,
)


class TestRepository(RepositoryContract):
    """Every dialect must pass all of it."""


class TestLockAndFetch(LockAndFetchContract):
    """Where the dialect can claim in the statement that selects."""


class TestResolution(ResolverContract):
    """What happens to a row whose task this instance does not have."""


class TestPaging(PagingContract):
    """Listing with a cursor, on every dialect - the ordering is the database's, not ours."""


class TestBatchAndPaging(BatchAndPagingContract):
    """Batch atomicity and cursor stability, on every dialect."""
