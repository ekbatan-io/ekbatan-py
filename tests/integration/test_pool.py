"""The pool, run the way Java's Hikari pool runs with Ekbatan's settings.

Measured on Java's pool (Hikari 7, as `ConnectionProvider` builds it): a pool of 3 holds 3 open
connections about 300 ms after it is built, with no query asked of it - `minimumIdle` defaults to
the maximum. A wrong password is retried - 10 ms, then twice as long each time up to 5 s - and the
caller fails only when `connectionTimeout` (30 s) is up. SQLite has neither a pool to fill nor a
password, so these run on the servers only.
"""

from __future__ import annotations

import dataclasses
import logging
import time

import pytest
from sqlalchemy import text

from ekbatan.persistence import engine_for
from fixtures.logins import data_source

MARKER = "Mk9-wrong-pw-marker-for-the-pool"


@pytest.fixture
def server_url(backend, request):
    if backend.dialect == "sqlite":
        pytest.skip("SQLite has no pool to fill and no password")
    from fixtures.migrating import fresh

    return fresh(backend, request)


def _wait_for(condition, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def test_a_pool_fills_to_its_size_without_being_asked(server_url):
    engine = engine_for(data_source(server_url, pool_size=3))
    try:
        assert _wait_for(lambda: engine.pool.checkedin() == 3, 5), engine.pool.status()
    finally:
        engine.dispose()


def test_a_pool_refills_after_a_connection_is_thrown_away(server_url):
    engine = engine_for(data_source(server_url, pool_size=2))
    try:
        assert _wait_for(lambda: engine.pool.checkedin() == 2, 5)
        with engine.connect() as conn:
            conn.execute(text("select 1"))
            conn.invalidate()
        assert _wait_for(lambda: engine.pool.checkedin() == 2, 5), engine.pool.status()
    finally:
        engine.dispose()


def test_a_disposed_pool_does_not_refill(server_url):
    engine = engine_for(data_source(server_url, pool_size=2))
    assert _wait_for(lambda: engine.pool.checkedin() == 2, 5)
    engine.dispose()
    time.sleep(0.5)
    assert engine.pool.checkedin() == 0, "disposing closes them, and nothing opens them again"
    engine.dispose()


def test_a_wrong_password_is_retried_until_the_pool_timeout_and_never_logged(server_url, caplog):
    """Java's pool fails a wrong password after `connectionTimeout`, retrying meanwhile; this one
    after `pool_timeout`, retrying with the same backoff. The password - a marker nobody else
    uses - is in no log line, at DEBUG, and not in the error."""
    caplog.set_level(logging.DEBUG)
    config = dataclasses.replace(
        data_source(server_url, pool_size=1, pool_timeout=2), password=MARKER
    )
    engine = engine_for(config)
    started = time.monotonic()
    try:
        with pytest.raises(Exception) as refused, engine.connect():
            pass
        took = time.monotonic() - started
    finally:
        engine.dispose()

    assert 1.9 <= took < 6, took
    attempts = caplog.text.count("connection add failed")
    assert attempts >= 3, "the pool kept trying while the caller waited"
    assert MARKER not in caplog.text
    assert MARKER not in str(refused.value)
    assert MARKER not in repr(refused.value)
