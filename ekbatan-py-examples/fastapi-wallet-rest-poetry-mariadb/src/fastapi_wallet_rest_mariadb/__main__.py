"""Run the service.

    make run          # starts the database in compose.yaml, and stops it after

Then:

    curl -XPOST localhost:8000/wallets -H 'content-type: application/json' -d '{
      "owner_id": "11111111-1111-1111-1111-111111111111",
      "currency": "EUR",
      "opening_balance": "100.00"
    }'

Migrations run at startup, so the first request works against an empty database.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    # The factory form, so uvicorn's reloader builds the app in the worker process rather than
    # inheriting a database pool across a fork - which is a real hazard: a forked child and its
    # parent would hand out the same sockets.
    uvicorn.run(
        "fastapi_wallet_rest_mariadb.api:create_app",
        factory=True,
        host=os.environ.get("WALLET_HOST", "127.0.0.1"),
        port=int(os.environ.get("WALLET_PORT", "8000")),
        reload=os.environ.get("WALLET_RELOAD") == "1",
        # The first cap on work in flight, before any of it reaches the app: past this many
        # open requests uvicorn answers 503 itself. The second is the request pool, ten
        # connections in `ekbatan.yaml`, where requests queue for at most `pool_timeout`.
        limit_concurrency=int(os.environ.get("WALLET_LIMIT_CONCURRENCY", "50")),
    )


if __name__ == "__main__":
    main()
