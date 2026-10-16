"""Central configuration for ChronosGraph.

All tunables are environment-driven so the same code runs against a local
FalkorDB container, FalkorDB Cloud, or the in-memory fallback used in tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
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
    prod_graph: str = os.getenv("CHRONOS_PROD_GRAPH", "prod_org_brain")
    memory_graph: str = os.getenv("CHRONOS_MEMORY_GRAPH", "agent_memory")
    shadow_prefix: str = os.getenv("CHRONOS_SHADOW_PREFIX", "shadow_sim_")

    # Traversal bounds. Multi-hop explosion is the main failure mode of
    # graph agents, so every traversal is capped.
    max_hops: int = _env_int("CHRONOS_MAX_HOPS", 6)
    traversal_node_budget: int = _env_int("CHRONOS_TRAVERSAL_BUDGET", 4000)

    # Decision thresholds.
    confidence_threshold: float = _env_float("CHRONOS_CONFIDENCE_THRESHOLD", 0.85)
    criticality_alert_threshold: float = _env_float(
        "CHRONOS_CRITICALITY_THRESHOLD", 0.30
    )

    # Live topology change window used by incident triage.
    recent_change_window_hours: int = _env_int("CHRONOS_CHANGE_WINDOW_HOURS", 2)

    # When true the engine refuses to touch a real FalkorDB and uses the
    # in-process backend. Tests set this; production never does.
    force_memory_backend: bool = os.getenv("CHRONOS_MEMORY_BACKEND", "").lower() in {
        "1",
        "true",
        "yes",
    }

    actions_dry_run: bool = os.getenv("CHRONOS_ACTIONS_DRY_RUN", "1").lower() not in {
        "0",
        "false",
        "no",
    }

    api_host: str = os.getenv("CHRONOS_API_HOST", "0.0.0.0")
    api_port: int = _env_int("CHRONOS_API_PORT", 8000)
    frontend_origin: str = os.getenv("CHRONOS_FRONTEND_ORIGIN", "*")

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
