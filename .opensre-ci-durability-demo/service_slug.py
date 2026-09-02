def service_slug(value: str) -> str:
    """Return the canonical slug used in service URLs."""
    return value.strip().lower()


if __name__ == "__main__":
    assert service_slug("Open SRE") == "open-sre", "spaces must become hyphens"
