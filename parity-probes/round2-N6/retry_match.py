"""Docs say retry matching is by exact class (concepts/actions.md:174, reference/action.mdx:161);
learn/actions.mdx:185 says isinstance. Run it."""
from ekbatan.action.executor import RetryConfig
from ekbatan.errors import EkbatanError, StaleRecordError
p = RetryConfig(on=EkbatanError, max_retries=1, delay=0)
import inspect
print("PROBE retry-config-methods:", [m for m in dir(p) if not m.startswith("_")])
print("PROBE EkbatanError-policy-matches-StaleRecordError:", p.matches(StaleRecordError("x")))
print("PROBE EkbatanError-policy-matches-EkbatanError:", p.matches(EkbatanError("x")))
