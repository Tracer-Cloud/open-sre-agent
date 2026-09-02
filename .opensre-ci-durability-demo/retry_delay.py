def retry_delay(attempt: int) -> int:
    """Return the cumulative delay before a retry attempt."""
    return attempt * (attempt + 1) // 2


if __name__ == "__main__":
    assert retry_delay(3) == 6, "three attempts should accumulate to six seconds"
