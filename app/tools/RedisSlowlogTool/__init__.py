"""Redis Slow Log Tool."""

from typing import Any

from app.integrations.redis import (
    RedisConfig,
    get_slowlog,
    redis_extract_params,
    redis_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_redis_slowlog",
    description=(
        "Retrieve recent Redis slow log entries, including the command, "
        "execution duration, and originating client, to surface slow commands."
    ),
    source="redis",
    surfaces=("investigation", "chat"),
    is_available=redis_is_available,
    extract_params=redis_extract_params,
)
def get_redis_slowlog(
    host: str,
    port: int = 6379,
    username: str = "",
    password: str = "",
    db: int = 0,
    ssl: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Fetch recent slow log entries from a Redis instance."""
    config = RedisConfig(host=host, port=port, username=username, password=password, db=db, ssl=ssl)
    return get_slowlog(config, limit=limit)
