"""Area A twin of ProbeRegistryTest.java: RepositoryRegistry, no database.

    uv run --no-sync python parity-probes/A-repository/probe_registry.py
"""

from __future__ import annotations

import common  # noqa: F401  (puts tests/ on sys.path)

from ekbatan.repository import RepositoryRegistry
from ekbatan.sharding import NoShardingStrategy
from fixtures.domain import Wallet


class Fake:
    model_type = Wallet
    sharding = NoShardingStrategy()

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name


def p(k, v):
    print(f"PROBE registry.{k}: {v}")


one, two = Fake("first"), Fake("second")
try:
    reg = RepositoryRegistry.builder().with_model_repository(one).with_model_repository(two).build()
    p("two_repos_one_model", f"no error -> {reg.repository(Wallet)}")
except Exception as e:  # noqa: BLE001
    p("two_repos_one_model.err", type(e).__name__)
try:
    reg = RepositoryRegistry.builder().with_model_repository(one).with_model_repository(one).build()
    p("same_repo_twice", f"no error -> {reg.repository(Wallet)}")
except Exception as e:  # noqa: BLE001
    p("same_repo_twice.err", type(e).__name__)
empty = RepositoryRegistry.builder().build()
try:
    p("lookup_unregistered", f"no error -> {empty.repository(Wallet)}")
except Exception as e:  # noqa: BLE001
    p("lookup_unregistered.err", type(e).__name__)
