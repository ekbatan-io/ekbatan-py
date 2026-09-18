"""The numbers the website states are the numbers this library has.

A default in a document is a claim with no compiler behind it. These are the ones a reader
acts on - pool sizes they copy into a config, poll delays they reason about latency from,
a retry count they decide whether to widen - and each one is stated on a page that nothing
else checks. Changing a default here without changing the page is the failure this catches.

The page each claim lives on is named, so a failure says what to edit.
"""

from datetime import timedelta

from ekbatan.action.executor import ExecutionConfiguration
from ekbatan.config import DataSourceConfig
from ekbatan.distributedjobs import DEFAULT_POLLING_INTERVAL, EKBATAN_HEARTBEAT_INTERVAL
from ekbatan.errors import StaleRecordError
from ekbatan.events.dispatch import DEFAULT_MAX_BACKOFF, DEFAULT_RETENTION
from ekbatan.events.jobs import (
    DEFAULT_FANOUT_BATCH_SIZE,
    DEFAULT_HANDLING_BATCH_SIZE,
    DEFAULT_POLL_DELAY,
)


def test_the_pool_defaults_the_setup_page_tabulates() -> None:
    # website/src/pages/learn/complete-project-setup.mdx, "Sizing the pool"
    pool = DataSourceConfig(url="sqlite://")
    assert pool.pool_size == 10
    assert pool.max_overflow == 0
    assert pool.pool_timeout == 30.0
    assert pool.pool_recycle == 1800
    assert pool.pool_pre_ping is True


def test_the_scheduler_defaults_the_jobs_page_tabulates() -> None:
    # website/src/pages/reference/jobs/distributed-jobs.mdx, "What the three knobs do" -
    # including the 10s x 6 = 60 seconds it works out from the heartbeat.
    polling, heartbeat = DEFAULT_POLLING_INTERVAL, EKBATAN_HEARTBEAT_INTERVAL
    assert polling == timedelta(seconds=10)
    assert heartbeat == timedelta(seconds=10)


def test_the_event_pipeline_defaults_the_handler_page_tabulates() -> None:
    # website/src/pages/reference/events/local-event-handler.mdx, "Configuration"
    poll, backoff, retention = DEFAULT_POLL_DELAY, DEFAULT_MAX_BACKOFF, DEFAULT_RETENTION
    assert poll == timedelta(seconds=1)
    assert DEFAULT_FANOUT_BATCH_SIZE == 200
    assert DEFAULT_HANDLING_BATCH_SIZE == 100
    assert backoff == timedelta(minutes=5)
    assert retention == timedelta(days=7)


def test_the_execution_defaults_the_action_pages_state() -> None:
    # website/src/pages/reference/action.mdx, "Cross-shard execution", and
    # website/src/pages/learn/actions.mdx on the one retry a lost version race gets.
    config = ExecutionConfiguration()
    assert config.allow_cross_shard is False
    (retry,) = config.retry_configs
    assert retry.on is StaleRecordError
    assert retry.max_retries == 1
    assert retry.delay == 0.1
