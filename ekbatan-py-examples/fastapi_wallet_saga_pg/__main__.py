"""Run the saga service.

    docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=ekbatan -e POSTGRES_USER=ekbatan \
      -e POSTGRES_DB=wallet postgres:17-alpine

    cd ekbatan-py-examples && uv run python -m fastapi_wallet_saga_pg

Open two wallets, transfer between them, and watch the saga advance:

    curl -XPOST localhost:8000/wallets/<a>/transfers -H 'content-type: application/json' \
      -d '{"to_wallet_id": "<b>", "amount": "30.00"}'      # 202, with a status_url
    curl localhost:8000/transfers/<id>                     # IN_FLIGHT
    curl -XPOST localhost:8000/transfers/deliver           # run the pipeline one round
    curl localhost:8000/transfers/<id>                     # COMPLETED, or REFUNDED
"""

from __future__ import annotations

import os

import uvicorn

URL = os.environ.get("WALLET_DATABASE_URL", "postgresql+psycopg://localhost:5432/wallet")
#: The login lives here, never in the url - a url is logged and repeated.
USERNAME = os.environ.get("WALLET_DATABASE_USERNAME", "ekbatan")
PASSWORD = os.environ.get("WALLET_DATABASE_PASSWORD", "ekbatan")


def main() -> None:
    from .api import create_app

    uvicorn.run(
        create_app(URL, username=USERNAME, password=PASSWORD),
        host=os.environ.get("WALLET_HOST", "127.0.0.1"),
        port=int(os.environ.get("WALLET_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
