"""The whole `ekbatan` section from a file: namespace, sharding, jobs and local-event-handler.

Java's examples keep all four in `application.yml`; `load_ekbatan_config` reads the same keys, in
every format. The sharding rules themselves are in `test_config_files.py`.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from ekbatan.config_files import EkbatanConfig, load_ekbatan_config, load_sharding_config
from ekbatan.distributedjobs import JobsConfig
from ekbatan.errors import ValidationError
from ekbatan.events import HandlingConfig, LocalEventHandlerConfig

SHARDING = {
    "defaultShard": {"group": 0, "member": 0},
    "groups": [
        {
            "group": 0,
            "name": "global",
            "members": [{"member": 0, "configs": {"primaryConfig": {"url": "sqlite:///w.db"}}}],
        }
    ],
}

#: Every key Java's `ekbatan` section has, as the Java examples spell them.
FULL = {
    "ekbatan": {
        "namespace": "example.wallet",
        "sharding": SHARDING,
        "jobs": {
            "pollingInterval": "PT10S",
            "heartbeat-interval": "PT30S",
            "shutdownMaxWait": "PT0S",
        },
        "local-event-handler": {
            "fanout-poll-delay": "PT0.5S",
            "fanoutBatchSize": 50,
            "handling-poll-delay": "PT1S",
            "handling-batch-size": 25,
            "handling-max-backoff-cap": "PT5M",
            "handlingRetentionWindow": "P7D",
            "handling": {"enabled": True},
        },
    },
    "logging": {"anything": "outside ekbatan is left alone"},
}

EXPECTED_JOBS = JobsConfig(
    polling_interval=timedelta(seconds=10),
    heartbeat_interval=timedelta(seconds=30),
    shutdown_max_wait=timedelta(0),
)
EXPECTED_EVENTS = LocalEventHandlerConfig(
    fanout_poll_delay=timedelta(milliseconds=500),
    fanout_batch_size=50,
    handling_poll_delay=timedelta(seconds=1),
    handling_batch_size=25,
    handling_max_backoff_cap=timedelta(minutes=5),
    handling_retention_window=timedelta(days=7),
    handling=HandlingConfig(enabled=True),
)

FULL_TOML = """
[ekbatan]
namespace = "example.wallet"

[ekbatan.sharding]
defaultShard = { group = 0, member = 0 }

[[ekbatan.sharding.groups]]
group = 0
name = "global"

[[ekbatan.sharding.groups.members]]
member = 0

[ekbatan.sharding.groups.members.configs.primaryConfig]
url = "sqlite:///w.db"

[ekbatan.jobs]
pollingInterval = "PT10S"
heartbeat-interval = "PT30S"
shutdownMaxWait = "PT0S"

[ekbatan.local-event-handler]
fanout-poll-delay = "PT0.5S"
fanoutBatchSize = 50
handling-poll-delay = "PT1S"
handling-batch-size = 25
handling-max-backoff-cap = "PT5M"
handlingRetentionWindow = "P7D"

[ekbatan.local-event-handler.handling]
enabled = true
"""

FULL_INI = """
[ekbatan]
namespace = example.wallet

[ekbatan.sharding.defaultShard]
group = 0
member = 0

[ekbatan.sharding.groups.0]
group = 0
name = global

[ekbatan.sharding.groups.0.members.0]
member = 0

[ekbatan.sharding.groups.0.members.0.configs.primaryConfig]
url = sqlite:///w.db

[ekbatan.jobs]
pollingInterval = PT10S
heartbeat-interval = PT30S
shutdownMaxWait = PT0S

[ekbatan.local-event-handler]
fanout-poll-delay = PT0.5S
fanoutBatchSize = 50
handling-poll-delay = PT1S
handling-batch-size = 25
handling-max-backoff-cap = PT5M
handlingRetentionWindow = P7D

[ekbatan.local-event-handler.handling]
enabled = true
"""


def load(tmp_path: Path, name: str, text: str, environ: dict[str, str] | None = None):
    file = tmp_path / name
    file.write_text(text)
    return load_ekbatan_config(file, environ=environ or {})


def refused(tmp_path: Path, name: str, text: str) -> str:
    with pytest.raises(ValidationError) as caught:
        load(tmp_path, name, text)
    return str(caught.value)


def yaml_of(section: dict[str, object]) -> str:
    return json.dumps({"ekbatan": {"sharding": SHARDING, **section}})


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("ekbatan.json", json.dumps(FULL)),
        # JSON is YAML, so the same text read by the YAML reader.
        ("ekbatan.yaml", json.dumps(FULL)),
        ("ekbatan.toml", FULL_TOML),
        ("ekbatan.ini", FULL_INI),
    ],
)
def test_every_format_reads_every_key_of_the_section(tmp_path, name, text):
    config = load(tmp_path, name, text)

    assert config == EkbatanConfig(
        namespace="example.wallet",
        sharding=load_sharding_config(tmp_path / name, environ={}),
        jobs=EXPECTED_JOBS,
        local_event_handler=EXPECTED_EVENTS,
    )


def test_a_section_with_only_sharding_gets_javas_defaults(tmp_path):
    config = load(tmp_path, "ekbatan.yaml", yaml_of({}))

    assert config.namespace == "default"
    assert config.jobs == JobsConfig.defaults()
    assert config.local_event_handler == LocalEventHandlerConfig.defaults()
    assert config.local_event_handler.handling.enabled is False


def test_handling_without_enabled_stays_off(tmp_path):
    config = load(tmp_path, "ekbatan.yaml", yaml_of({"local-event-handler": {"handling": {}}}))

    assert config.local_event_handler.handling.enabled is False


def test_the_namespace_takes_a_placeholder(tmp_path):
    text = yaml_of({"namespace": "${SERVICE_NAMESPACE:example.default}"})

    assert load(tmp_path, "e.yaml", text).namespace == "example.default"
    assert load(tmp_path, "e.yaml", text, {"SERVICE_NAMESPACE": "x.y"}).namespace == "x.y"


def test_a_duration_takes_a_placeholder(tmp_path):
    text = yaml_of({"jobs": {"polling-interval": "${POLL:PT2S}"}})

    assert load(tmp_path, "e.yaml", text).jobs.polling_interval == timedelta(seconds=2)


def test_sharding_is_required(tmp_path):
    message = refused(tmp_path, "e.yaml", json.dumps({"ekbatan": {"namespace": "a"}}))

    assert message == "e.yaml: ekbatan: sharding is required"


def test_a_file_without_ekbatan_says_so(tmp_path):
    assert refused(tmp_path, "e.yaml", "other: 1\n") == "e.yaml: no 'ekbatan' in the file"


@pytest.mark.parametrize(
    ("section", "path"),
    [
        ({"namespaces": "a"}, "e.yaml: ekbatan"),
        ({"jobs": {"poll-interval": "PT1S"}}, "e.yaml: ekbatan.jobs"),
        ({"local-event-handler": {"batch-size": 1}}, "e.yaml: ekbatan.local-event-handler"),
        (
            {"local-event-handler": {"handling": {"enable": True}}},
            "e.yaml: ekbatan.local-event-handler.handling",
        ),
    ],
)
def test_an_unknown_key_under_ekbatan_is_refused_with_its_path(tmp_path, section, path):
    message = refused(tmp_path, "e.yaml", yaml_of(section))

    assert message.startswith(f"{path}: unknown key"), message


@pytest.mark.parametrize("written", ["10s", "10", "PT", "P1H", "ten seconds"])
def test_a_duration_must_be_iso_8601_as_java_reads_it(tmp_path, written):
    message = refused(tmp_path, "e.yaml", yaml_of({"jobs": {"polling-interval": written}}))

    assert message == (
        "e.yaml: ekbatan.jobs.polling-interval: must be an ISO-8601 duration such as PT10S, "
        "PT0.5S or PT5M, as Java reads it"
    )


def test_a_bare_number_is_not_a_duration(tmp_path):
    message = refused(tmp_path, "e.yaml", yaml_of({"local-event-handler": {"fanoutPollDelay": 5}}))

    assert "ekbatan.local-event-handler.fanoutPollDelay: must be an ISO-8601 duration" in message


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("PT10S", timedelta(seconds=10)),
        ("pt0.5s", timedelta(milliseconds=500)),
        ("PT0,25S", timedelta(milliseconds=250)),
        ("P1DT2H", timedelta(days=1, hours=2)),
        ("PT1H30M", timedelta(hours=1, minutes=30)),
        ("+PT3S", timedelta(seconds=3)),
        ("PT5.S", timedelta(seconds=5)),
        ("-PT-2S", timedelta(seconds=2)),
    ],
)
def test_durations_read_as_javas_duration_parse_reads_them(tmp_path, written, expected):
    # Each pair checked against `java.time.Duration.parse` on JDK 25 when this was written.
    config = load(tmp_path, "e.yaml", yaml_of({"jobs": {"heartbeat-interval": written}}))

    assert config.jobs.heartbeat_interval == expected


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("PT-0.5S", timedelta(milliseconds=-500)),
        ("PT-1.5S", timedelta(milliseconds=-1500)),
        ("-PT5M", timedelta(minutes=-5)),
    ],
)
def test_negative_durations_read_as_javas_duration_parse_reads_them(written, expected):
    # Straight at the reader: every duration key refuses a negative value, rightly, so none
    # can carry one through a file. Checked against `Duration.parse` like the ones above.
    from ekbatan.config_files import _duration

    assert _duration(written, "x") == expected


def test_the_config_classes_refusals_carry_the_path(tmp_path):
    message = refused(tmp_path, "e.yaml", yaml_of({"jobs": {"polling-interval": "PT0S"}}))

    assert message.startswith("e.yaml: ekbatan.jobs: "), message
    assert "polling-interval" in message


def test_a_count_must_be_a_whole_number(tmp_path):
    message = refused(
        tmp_path, "e.yaml", yaml_of({"local-event-handler": {"fanout-batch-size": "many"}})
    )

    assert message == (
        "e.yaml: ekbatan.local-event-handler.fanout-batch-size: must be a whole number"
    )


def test_ini_enabled_is_read_strictly(tmp_path):
    text = FULL_INI.replace("enabled = true", "enabled = yes")

    message = refused(tmp_path, "e.ini", text)

    assert message == "e.ini: ekbatan.local-event-handler.handling.enabled: must be true or false"
