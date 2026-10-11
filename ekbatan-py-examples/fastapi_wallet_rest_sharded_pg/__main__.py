"""Run the sharded service.

Two databases on one server is enough to see the routing - the framework opens two pools
against two urls and cannot tell that one process is behind both:

    docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=ekbatan -e POSTGRES_USER=ekbatan \
      -e POSTGRES_DB=postgres postgres:17-alpine
    psql ... -c 'CREATE DATABASE shard_global' -c 'CREATE DATABASE shard_mexico'

    cd ekbatan-py-examples && uv run python -m fastapi_wallet_rest_sharded_pg

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

from .actions import GLOBAL, MEXICO

BASE = os.environ.get("WALLET_BASE_URL", "postgresql+psycopg://localhost:5432")
#: The login lives here, never in a url - a url is logged and repeated.
USERNAME = os.environ.get("WALLET_DATABASE_USERNAME", "ekbatan")
PASSWORD = os.environ.get("WALLET_DATABASE_PASSWORD", "ekbatan")

#: The whole layout. Adding a shard is one more entry; nothing above this file changes.
LAYOUT = {
    GLOBAL: os.environ.get("WALLET_GLOBAL_URL", f"{BASE}/shard_global"),
    MEXICO: os.environ.get("WALLET_MEXICO_URL", f"{BASE}/shard_mexico"),
}


def main() -> None:
    from .api import create_app

    uvicorn.run(
        create_app(LAYOUT, username=USERNAME, password=PASSWORD),
        host=os.environ.get("WALLET_HOST", "127.0.0.1"),
        port=int(os.environ.get("WALLET_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
