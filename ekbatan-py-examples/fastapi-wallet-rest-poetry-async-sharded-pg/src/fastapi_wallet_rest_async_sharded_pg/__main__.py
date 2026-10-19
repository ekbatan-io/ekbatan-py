"""Run the sharded service, on native asyncio.

Two databases on one server is enough to see the routing - the framework opens two pools
against two urls and cannot tell that one process is behind both:

    docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=ekbatan -e POSTGRES_USER=ekbatan \
      -e POSTGRES_DB=postgres postgres:17-alpine
    psql ... -c 'CREATE DATABASE shard_global' -c 'CREATE DATABASE shard_mexico'

    make run

Then open a wallet in each and watch the ids carry their shard:

    curl -XPOST localhost:8000/wallets -H 'content-type: application/json' -d '{
      "owner_id": "11111111-1111-1111-1111-111111111111",
      "currency": "EUR", "opening_balance": "100.00", "country": "MX"
    }'
    curl localhost:8000/shards
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    from .api import create_app

    # The layout, the login and the namespace are in `ekbatan.yaml`; its placeholders read
    # WALLET_GLOBAL_URL, WALLET_MEXICO_URL, WALLET_DATABASE_USERNAME and WALLET_DATABASE_PASSWORD.
    uvicorn.run(
        create_app(),
        host=os.environ.get("WALLET_HOST", "127.0.0.1"),
        port=int(os.environ.get("WALLET_PORT", "8000")),
        # The first cap on work in flight, before any of it reaches the app: past this many
        # open requests uvicorn answers 503 itself. The second is each shard's request pool,
        # ten connections in `ekbatan.yaml`, where requests queue for at most `pool_timeout`.
        limit_concurrency=int(os.environ.get("WALLET_LIMIT_CONCURRENCY", "50")),
    )


if __name__ == "__main__":
    main()
