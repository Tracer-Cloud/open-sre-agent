"""OpenSRE test inventory and interactive selection helpers."""

from surfaces.cli.tests.catalog import TestCatalog, TestCatalogItem, TestRequirement
from surfaces.cli.tests.discover import load_test_catalog
from surfaces.cli.tests.runner import (
    find_test_item,
    format_command,
    run_catalog_item,
    run_catalog_items,
)

__all__ = [
    "TestCatalog",
    "TestCatalogItem",
    "TestRequirement",
    "find_test_item",
    "format_command",
    "load_test_catalog",
    "run_catalog_item",
    "run_catalog_items",
]
