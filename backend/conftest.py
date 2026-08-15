"""
Shared pytest fixtures.

catalog_store.get_catalog_entries() keeps a small in-process cache (see
its docstring) so a single scan doesn't re-hit the DB per spine. That
cache is a plain module-level dict, so it doesn't know about Django
TestCase's per-test transaction rollback -- without this, a test that
populates CatalogBook rows can read a stale (e.g. empty) cache left
behind by an earlier test that queried the catalog before any rows
existed. Autouse fixture keeps every test starting from a clean cache.
"""
import pytest


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    from catalog import catalog_store

    catalog_store.clear_cache()
    yield
    catalog_store.clear_cache()
