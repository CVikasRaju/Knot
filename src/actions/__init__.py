"""Action layer: diff parsing, shadow simulation, remediation, expertise."""

from .diff_parser import ParsedFile, build_mutation_spec, parse_unified_diff
from .expertise import ExpertiseRanker, ExpertScore
from .remediation import (
    ActionRefused,
    ActionResult,
    GitHubActionClient,
    RemediationExecutor,
)
from .shadow_sim import (
    AdrViolation,
    MutationSpec,
    ShadowSimulator,
    SimulationResult,
)

__all__ = [
    "ActionRefused",
    "ActionResult",
    "AdrViolation",
    "ExpertScore",
    "ExpertiseRanker",
    "GitHubActionClient",
    "MutationSpec",
    "ParsedFile",
    "RemediationExecutor",
    "ShadowSimulator",
    "SimulationResult",
    "build_mutation_spec",
    "parse_unified_diff",
]
