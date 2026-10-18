#!/bin/bash
# README.md:202-203,269 test counts, README.md:71 dependency list, README.md:262 example count.
cd "$(dirname "$0")/../.."
c() { uv run --no-sync pytest "$@" --collect-only -q -p no:cacheprovider 2>&1 | awk -F': ' '/: [0-9]+$/{s+=$2} END{print s}'; }
echo "PROBE unit-tests: $(c tests/unit)   (README says 250)"
echo "PROBE integration-tests: $(c tests/integration)   (README says 322)"
echo "PROBE all-tests: $(c tests)   (README says 578)"
echo "PROBE dialects-all: $(c tests --dialects=all)   (README says 1,370)"
echo "PROBE examples: $(find ekbatan-py-examples -mindepth 1 -maxdepth 1 -type d -not -name ".*" -not -name "__pycache__" | wc -l | tr -d ' ')   (README says 21)"
echo "PROBE unconditional-deps: $(python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies'])")   (README says SQLAlchemy and db-scheduler only)"
