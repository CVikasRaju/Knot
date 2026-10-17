"""Central configuration for Knot.

All tunables are environment-driven so the same code runs against a local
FalkorDB container, FalkorDB Cloud, or the in-memory fallback used in tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env_str(name: str, default: str = "") -> str:
    """Read ``KNOT_<name>``, falling back to the legacy ``CHRONOS_<name>``.

    The product was renamed from ChronosGraph to Knot; the old prefix still
    works so an existing ``.env`` does not break.
    """
    return os.getenv(f"KNOT_{name}", os.getenv(f"CHRONOS_{name}", default))


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_str(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    """Runtime settings resolved from the environment."""

    # 127.0.0.1 rather than "localhost": inside containers "localhost" often
    # resolves to IPv6 ::1 while the FalkorDB port is published on IPv4 only.
    falkor_host: str = os.getenv("FALKORDB_HOST", "127.0.0.1")
    falkor_port: int = _env_int("FALKORDB_PORT", 6379)
    falkor_password: str | None = os.getenv("FALKORDB_PASSWORD") or None
    falkor_username: str | None = os.getenv("FALKORDB_USERNAME") or None

    # Graph tenant names. FalkorDB isolates tenants by graph name, which is
    # what makes the shadow-graph simulation cheap and non-destructive.
    prod_graph: str = _env_str("PROD_GRAPH", "prod_org_brain")
    memory_graph: str = _env_str("MEMORY_GRAPH", "agent_memory")
    shadow_prefix: str = _env_str("SHADOW_PREFIX", "shadow_sim_")

    # Traversal bounds. Multi-hop explosion is the main failure mode of
    # graph agents, so every traversal is capped.
    max_hops: int = _env_int("MAX_HOPS", 6)
    traversal_node_budget: int = _env_int("TRAVERSAL_BUDGET", 4000)

    # Decision thresholds.
    confidence_threshold: float = _env_float("CONFIDENCE_THRESHOLD", 0.85)
    criticality_alert_threshold: float = _env_float("CRITICALITY_THRESHOLD", 0.30)

    # Live topology change window used by incident triage.
    recent_change_window_hours: int = _env_int("CHANGE_WINDOW_HOURS", 2)

    # When true the engine refuses to touch a real FalkorDB and uses the
    # in-process backend. Tests set this; production never does.
    force_memory_backend: bool = _env_str("MEMORY_BACKEND").lower() in {
        "1",
        "true",
        "yes",
    }

    # Knot's product behaviour is graph traversal, so a missing FalkorDB is a
    # hard failure by default rather than a silent degradation to an empty
    # in-process graph. Only tests opt in to that.
    allow_memory_fallback: bool = _env_str("ALLOW_MEMORY_FALLBACK").lower() in {
        "1",
        "true",
        "yes",
    }

    actions_dry_run: bool = _env_str("ACTIONS_DRY_RUN", "1").lower() not in {
        "0",
        "false",
        "no",
    }

    api_host: str = _env_str("API_HOST", "0.0.0.0")
    api_port: int = _env_int("API_PORT", 8000)
    frontend_origin: str = _env_str("FRONTEND_ORIGIN", "*")

    def shadow_graph_name(self, simulation_id: str) -> str:
        return f"{self.shadow_prefix}{simulation_id}"


@dataclass
class RuntimeContext:
    """Mutable per-request context threaded through the agent swarm."""

    session_id: str
    graph_name: str
    tags: dict[str, str] = field(default_factory=dict)


def get_settings() -> Settings:
    return Settings()
