"""Run the service.

    docker compose up -d          # or any Postgres
    uv run python -m examples.fastapi_wallet_rest_mysql

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
        "examples.fastapi_wallet_rest_mysql.api:create_app",
        factory=True,
        host=os.environ.get("WALLET_HOST", "127.0.0.1"),
        port=int(os.environ.get("WALLET_PORT", "8000")),
        reload=os.environ.get("WALLET_RELOAD") == "1",
    )


if __name__ == "__main__":
    main()
