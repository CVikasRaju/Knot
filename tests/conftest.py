"""Shared test fixtures.

Tests run against a **real FalkorDB instance** rather than a mock. That is
deliberate: the interesting failure modes in this project were all in the
boundary with the engine (path edge indices, header shapes, GRAPH.DELETE
semantics), and none of them would have been caught by a fake store.

Each test gets its own throwaway tenant names, so tests are isolated from each
other and from the developer's seeded demo graph.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from src.config import Settings, get_settings
from src.graph import open_store
from src.seed import seed_all


@pytest.fixture(scope="session")
def store():
    """A live store, or skip the suite when no backend is reachable."""
    settings = get_settings()
    backend = open_store(settings)
    if backend.name != "falkordb":
        pytest.skip(
            "FalkorDB is not reachable; start it with `docker compose up -d falkordb`."
        )
    return backend


@pytest.fixture
def settings(store) -> Settings:
    """Settings pointing at per-test tenant names."""
    base = get_settings()
    suffix = uuid.uuid4().hex[:8]
    return replace(
        base,
        prod_graph=f"t_prod_{suffix}",
        memory_graph=f"t_mem_{suffix}",
        shadow_prefix=f"t_shadow_{suffix}_",
    )


@pytest.fixture
def seeded(store, settings):
    """A fully seeded production graph plus an empty memory graph."""
    seed_all(store, settings, reset=True)
    yield store, settings
    for name in store.list_graphs():
        if name.startswith("t_"):
            store.drop_graph(name)


@pytest.fixture
def shadow_graphs_clean(store):
    """Fail the test if it leaks a shadow tenant."""
    before = set(store.list_graphs())
    yield
    leaked = {
        name for name in set(store.list_graphs()) - before
        if "shadow" in name
    }
    assert not leaked, f"test leaked shadow tenant(s): {leaked}"
