"""Redis Key Scan Tool."""

from typing import Any

from app.integrations.redis import (
    RedisConfig,
    redis_extract_params,
    redis_is_available,
    scan_keys,
)
from app.tools.tool_decorator import tool


@tool(
    name="scan_redis_keys",
    description=(
        "Count Redis keys matching a glob pattern and sample their TTL and type. "
        "Uses the non-blocking SCAN cursor (never KEYS) and is safe on large keyspaces."
    ),
    source="redis",
    surfaces=("investigation", "chat"),
    is_available=redis_is_available,
    extract_params=redis_extract_params,
)
def scan_redis_keys(
    host: str,
    port: int = 6379,
    username: str = "",
    password: str = "",
    db: int = 0,
    ssl: bool = False,
    pattern: str = "*",
    sample_limit: int | None = None,
) -> dict[str, Any]:
    """Count and sample Redis keys matching a pattern."""
    config = RedisConfig(host=host, port=port, username=username, password=password, db=db, ssl=ssl)
    return scan_keys(config, pattern=pattern, sample_limit=sample_limit)
