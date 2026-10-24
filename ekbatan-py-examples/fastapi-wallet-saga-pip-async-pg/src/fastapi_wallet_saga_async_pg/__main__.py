"""Run the saga service.

    make run          # starts the database in compose.yaml, and stops it after

Open two wallets, transfer between them, and watch the saga advance:

    curl -XPOST localhost:8000/wallets/transfers -H 'content-type: application/json' \
      -d '{"fromWalletId": "<a>", "toWalletId": "<b>", "amount": "25.00"}'   # 202
    curl localhost:8000/wallets/<b>                     # credited once step two has run
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    from .api import create_app

    # The database, its login and the namespace are in `ekbatan.yaml`; its placeholders read
    # WALLET_DATABASE_URL, WALLET_DATABASE_USERNAME and WALLET_DATABASE_PASSWORD.
    uvicorn.run(
        create_app(),
        host=os.environ.get("WALLET_HOST", "127.0.0.1"),
        port=int(os.environ.get("WALLET_PORT", "8000")),
        # The first cap on work in flight, before any of it reaches the app: past this many
        # open requests uvicorn answers 503 itself. The second is the request pool, ten
        # connections in `ekbatan.yaml`, where requests queue for at most `pool_timeout`.
        limit_concurrency=int(os.environ.get("WALLET_LIMIT_CONCURRENCY", "50")),
    )


if __name__ == "__main__":
    main()
