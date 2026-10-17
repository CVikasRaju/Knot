"""Dynamic expertise and ownership scoring (Track 03).

A static CODEOWNERS file tells you who *was* responsible when it was written.
Knot computes who actually knows the system right now, from the graph:
commit recency, review activity, architectural authorship and on-call state.

The scoring is a transparent weighted sum with an explicit recency decay, not a
learned model, so an engineer can be shown exactly why they were paged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from ..graph.store import GraphStore

#: Weight of each signal in the final ownership score.
WEIGHTS = {
    "authorship": 0.40,
    "review": 0.20,
    "adr_authorship": 0.20,
    "recency": 0.15,
    "on_call": 0.05,
}

#: Half-life in days for the recency term.
RECENCY_HALF_LIFE_DAYS = 45.0


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _recency_factor(timestamp: str | None) -> float:
    """Exponential decay: 1.0 today, 0.5 after one half-life."""
    parsed = _parse_ts(timestamp)
    if parsed is None:
        return 0.0
    age_days = max((datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0, 0.0)
    return round(math.pow(0.5, age_days / RECENCY_HALF_LIFE_DAYS), 4)


@dataclass
class ExpertScore:
    person: dict
    score: float
    signals: dict
    explanation: str

    def to_dict(self) -> dict:
        return {
            "person": self.person,
            "handle": self.person.get("handle"),
            "name": self.person.get("name"),
            "team": self.person.get("team"),
            "ownership_score": round(self.score, 4),
            "signals": self.signals,
            "explanation": self.explanation,
        }


class ExpertiseRanker:
    """Ranks people by how much they actually know about a service."""

    def __init__(self, store: GraphStore, prod_graph: str) -> None:
        self.store = store
        self.prod_graph = prod_graph
        self._cache: dict[str, dict] = {}

    def _load(self) -> None:
        """Read the whole people/provenance subgraph once and index it."""
        if self._cache:
            return
        snapshot = self.store.snapshot(self.prod_graph, limit=8000)
        people = {n["id"]: dict(n) for n in snapshot.nodes if n.get("label") == "Person"}
        teams = {n["id"]: dict(n) for n in snapshot.nodes if n.get("label") == "Team"}
        adrs = {n["id"]: dict(n) for n in snapshot.nodes if n.get("label") == "ADR"}
        prs = {
            n["id"]: dict(n)
            for n in snapshot.nodes
            if n.get("label") == "PullRequest"
        }
        commits = {
            n["id"]: dict(n) for n in snapshot.nodes if n.get("label") == "Commit"
        }

        # person -> team
        for edge in snapshot.edges:
            if edge["type"] == "MEMBER_OF" and edge["source"] in people:
                team = teams.get(edge["target"])
                if team:
                    people[edge["source"]]["team"] = team.get("name")
                    people[edge["source"]]["team_id"] = edge["target"]

        # team -> owned services
        owned: dict[str, list[str]] = {}
        for edge in snapshot.edges:
            if edge["type"] == "OWNS":
                owned.setdefault(edge["target"], []).append(edge["source"])
        # service -> owning team ids
        service_teams: dict[str, list[str]] = {}
        for service_id, team_ids in owned.items():
            service_teams[service_id] = team_ids

        # module -> services implementing it
        module_services: dict[str, list[str]] = {}
        for edge in snapshot.edges:
            if edge["type"] == "IMPLEMENTED_BY":
                module_services.setdefault(edge["target"], []).append(edge["source"])
        # PR -> modules
        pr_modules: dict[str, list[str]] = {}
        for edge in snapshot.edges:
            if edge["type"] == "MODIFIES":
                pr_modules.setdefault(edge["source"], []).append(edge["target"])

        # person -> authored PRs / commits / ADRs
        authored: dict[str, list[str]] = {}
        reviewed: dict[str, list[str]] = {}
        for edge in snapshot.edges:
            if edge["type"] == "AUTHORED":
                authored.setdefault(edge["source"], []).append(edge["target"])
            elif edge["type"] == "REVIEWED":
                reviewed.setdefault(edge["source"], []).append(edge["target"])

        # service -> ADR ids governing it
        adr_services: dict[str, list[str]] = {}
        for edge in snapshot.edges:
            if edge["type"] == "GOVERNS":
                adr_services.setdefault(edge["target"], []).append(edge["source"])

        self._cache = {
            "people": people,
            "teams": teams,
            "adrs": adrs,
            "prs": prs,
            "commits": commits,
            "service_teams": service_teams,
            "module_services": module_services,
            "pr_modules": pr_modules,
            "authored": authored,
            "reviewed": reviewed,
            "adr_services": adr_services,
        }

    def rank(self, service_id: str, top_k: int = 5) -> list[ExpertScore]:
        self._load()
        people = self._cache["people"]
        service_teams = self._cache["service_teams"].get(service_id, [])
        adr_ids = set(self._cache["adr_services"].get(service_id, []))

        # Everything downstream/around the service counts, so gather the
        # modules it owns and the PRs that touched them.
        relevant_modules: set[str] = set()
        for module_id, services in self._cache["module_services"].items():
            if service_id in services:
                relevant_modules.add(module_id)
        relevant_prs: set[str] = set()
        for pr_id, modules in self._cache["pr_modules"].items():
            if relevant_modules & set(modules):
                relevant_prs.add(pr_id)

        scores: list[ExpertScore] = []
        for person_id, person in people.items():
            team_id = person.get("team_id")
            authored_prs = set(self._cache["authored"].get(person_id, []))
            reviewed_prs = set(self._cache["reviewed"].get(person_id, []))

            authored_relevant = authored_prs & relevant_prs
            reviewed_relevant = reviewed_prs & relevant_prs
            authored_adrs = authored_prs & adr_ids

            # Recency: newest commit timestamp across authored PRs.
            newest_ts = None
            for pr_id in authored_relevant:
                pr = self._cache["prs"].get(pr_id, {})
                ts = pr.get("created_at")
                if ts and (newest_ts is None or str(ts) > str(newest_ts)):
                    newest_ts = ts
            recency = _recency_factor(newest_ts)

            on_team = bool(team_id and team_id in service_teams)
            on_call = bool(person.get("on_call"))

            authorship = min(len(authored_relevant) / 4.0, 1.0)
            review = min(len(reviewed_relevant) / 6.0, 1.0)
            adr_auth = min(len(authored_adrs) / 2.0, 1.0)

            raw = (
                WEIGHTS["authorship"] * authorship
                + WEIGHTS["review"] * review
                + WEIGHTS["adr_authorship"] * adr_auth
                + WEIGHTS["recency"] * recency
                + WEIGHTS["on_call"] * (1.0 if on_call else 0.0)
            )
            # Team membership is a gate, not an additive term: someone outside
            # the owning team can still rank highly via authorship, but a small
            # boost keeps the owning team preferred on a tie.
            if on_team:
                raw = min(raw * 1.15, 1.0)

            signals = {
                "authored_relevant_prs": sorted(authored_relevant),
                "reviewed_relevant_prs": sorted(reviewed_relevant),
                "authored_adrs": sorted(authored_adrs),
                "relevant_modules": sorted(relevant_modules),
                "recency_factor": recency,
                "newest_activity": newest_ts,
                "on_owning_team": on_team,
                "on_call": on_call,
            }
            explanation = (
                f"{person.get('handle')} authored {len(authored_relevant)} of "
                f"{len(relevant_prs)} PR(s) touching this service's modules"
                + (
                    f", reviewed {len(reviewed_relevant)}"
                    if reviewed_relevant
                    else ""
                )
                + (
                    f", authored ADR(s) {', '.join(sorted(authored_adrs))}"
                    if authored_adrs
                    else ""
                )
                + f". Recency {recency:.2f}"
                + (", on the owning team" if on_team else ", external to owning team")
                + (", currently on-call." if on_call else ".")
            )
            scores.append(
                ExpertScore(
                    person=person,
                    score=raw,
                    signals=signals,
                    explanation=explanation,
                )
            )

        scores.sort(key=lambda s: s.score, reverse=True)
        return [s for s in scores if s.score > 0][:top_k] or scores[:top_k]

    def best_contact(self, service_id: str) -> ExpertScore | None:
        """Prefer an on-call owner; fall back to the highest scorer."""
        ranked = self.rank(service_id, top_k=10)
        for candidate in ranked:
            if candidate.signals.get("on_call") and candidate.signals.get(
                "on_owning_team"
            ):
                return candidate
        return ranked[0] if ranked else None
