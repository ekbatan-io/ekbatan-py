"""Run the service.

    make run          # starts the two databases in compose.yaml, and stops them after

Then:

    curl -XPOST localhost:8000/wallets -H 'content-type: application/json' -d '{
      "countryCode": "MX",
      "ownerId": "11111111-1111-1111-1111-111111111111",
      "currency": "MXN",
      "initialBalance": "100.00"
    }'

Migrations run at startup, on every shard, so the first request works against empty databases.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    # The factory form, so uvicorn's reloader builds the app in the worker process rather than
    # inheriting a database pool across a fork - which is a real hazard: a forked child and its
    # parent would hand out the same sockets.
    uvicorn.run(
        "fastapi_wallet_rest_pg.api:create_app",
        factory=True,
        host=os.environ.get("WALLET_HOST", "127.0.0.1"),
        port=int(os.environ.get("WALLET_PORT", "8000")),
        reload=os.environ.get("WALLET_RELOAD") == "1",
        # The first cap on work in flight, before any of it reaches the app: past this many
        # open requests uvicorn answers 503 itself.
        limit_concurrency=int(os.environ.get("WALLET_LIMIT_CONCURRENCY", "50")),
    )


if __name__ == "__main__":
    main()
