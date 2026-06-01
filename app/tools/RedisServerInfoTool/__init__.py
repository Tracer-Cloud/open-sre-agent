"""Redis Server Info Tool."""

from typing import Any

from app.integrations.redis import (
    RedisConfig,
    get_server_info,
    redis_extract_params,
    redis_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_redis_server_info",
    description=(
        "Retrieve Redis server info including memory usage, connected clients, "
        "keyspace statistics, and hit/miss and eviction counters."
    ),
    source="redis",
    surfaces=("investigation", "chat"),
    is_available=redis_is_available,
    extract_params=redis_extract_params,
)
def get_redis_server_info(
    host: str,
    port: int = 6379,
    username: str = "",
    password: str = "",
    db: int = 0,
    ssl: bool = False,
) -> dict[str, Any]:
    """Fetch server info metrics from a Redis instance."""
    config = RedisConfig(host=host, port=port, username=username, password=password, db=db, ssl=ssl)
    return get_server_info(config)
