"""N3: which meter scope the Python-only Redis release-failure counter lands under.

Java's ekbatan-keyed-lock-redis has no meter at all; this prints the Python counter's scope.
Run: uv run --no-sync python parity-probes/round2-N3/redis_metric_probe.py
"""

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

reader = InMemoryMetricReader()
metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))

from ekbatan.locks.redis import RELEASES_FAILED  # noqa: E402

RELEASES_FAILED.add(1)
for rm in reader.get_metrics_data().resource_metrics:
    for sm in rm.scope_metrics:
        for m in sm.metrics:
            print(f"PROBE metric.{m.name}: scope={sm.scope.name} unit={m.unit} desc={m.description}")
