"""Redis Replication Status Tool."""

from typing import Any

from app.integrations.redis import (
    RedisConfig,
    get_replication,
    redis_extract_params,
    redis_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_redis_replication",
    description=(
        "Retrieve Redis replication status: node role, master link health, "
        "connected replicas, and per-replica offset lag."
    ),
    source="redis",
    surfaces=("investigation", "chat"),
    is_available=redis_is_available,
    extract_params=redis_extract_params,
)
def get_redis_replication(
    host: str,
    port: int = 6379,
    username: str = "",
    password: str = "",
    db: int = 0,
    ssl: bool = False,
) -> dict[str, Any]:
    """Fetch replication status and replica lag from a Redis instance."""
    config = RedisConfig(host=host, port=port, username=username, password=password, db=db, ssl=ssl)
    return get_replication(config)
