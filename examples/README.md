# Examples

Standalone applications. Each one runs on its own, imports only `ekbatan`, and is imported by
nothing.

That last part is deliberate. The framework's own tests have their own domain, in
`tests/fixtures/`, which exists to exercise every shape worth exercising - including a few no
application would want. If the tests read from here instead, every change made for coverage
would land in the documentation and every change made for clarity would break the suite. So
they do not meet, and `tests/test_structure.py` keeps it that way.

| | |
|---|---|
| [`wallet_service/`](wallet_service/) | one wallet, end to end: a model that emits events, actions that stage them, an in-process handler that reacts, and a scheduled job. Runs on SQLite in one command. |

```
uv run python -m examples.wallet_service
```
