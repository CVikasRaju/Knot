"""Turn a pull-request diff into a structural :class:`MutationSpec`.

This is the bridge between "text a developer wrote" and "structure the graph can
reason about". It is intentionally conservative: when the parser cannot prove
that a change is structural it emits nothing rather than guessing, because a
false positive here becomes a false production block later.

Parsing is AST-based via ``tree-sitter`` rather than regex-based, so a rename of
a local variable does not register as an API change while a genuine signature
change does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from .shadow_sim import MutationSpec

try:  # pragma: no cover - exercised only when the grammar is installed
    import tree_sitter
    import tree_sitter_python
    import tree_sitter_javascript

    _PY_LANGUAGE = tree_sitter.Language(tree_sitter_python.language())
    _JS_LANGUAGE = tree_sitter.Language(tree_sitter_javascript.language())
    _TS_AVAILABLE = True
except Exception:  # noqa: BLE001 - degrade to heuristics, never crash
    _TS_AVAILABLE = False


@dataclass
class ParsedFile:
    path: str
    language: str
    added_lines: list[str]
    removed_lines: list[str]
    added_functions: list[str]
    removed_functions: list[str]
    changed_signatures: list[str]
    added_imports: list[str]

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "language": self.language,
            "added_functions": self.added_functions,
            "removed_functions": self.removed_functions,
            "changed_signatures": self.changed_signatures,
            "added_imports": self.added_imports,
        }


def parse_unified_diff(diff_text: str) -> list[ParsedFile]:
    """Split a unified diff into per-file added/removed line sets."""
    files: list[ParsedFile] = []
    current: ParsedFile | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:].strip()
            current = ParsedFile(
                path=path,
                language=_language_for(path),
                added_lines=[],
                removed_lines=[],
                added_functions=[],
                removed_functions=[],
                changed_signatures=[],
                added_imports=[],
            )
            files.append(current)
            continue
        if current is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            current.added_lines.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            current.removed_lines.append(line[1:])

    for parsed in files:
        _analyse(parsed)
    return files


def _language_for(path: str) -> str:
    if path.endswith(".py"):
        return "python"
    if path.endswith((".js", ".jsx", ".ts", ".tsx")):
        return "javascript"
    if path.endswith((".tf", ".yaml", ".yml", ".json")):
        return "config"
    return "other"


def _analyse(parsed: ParsedFile) -> None:
    """Populate function/import/signature deltas for one file."""
    if parsed.language in ("python", "javascript") and _TS_AVAILABLE:
        parsed.added_functions = _top_level_defs(
            "\n".join(parsed.added_lines), parsed.language
        )
        parsed.removed_functions = _top_level_defs(
            "\n".join(parsed.removed_lines), parsed.language
        )
    else:
        parsed.added_functions = _regex_defs("\n".join(parsed.added_lines))
        parsed.removed_functions = _regex_defs("\n".join(parsed.removed_lines))

    parsed.added_imports = [
        line.strip()
        for line in parsed.added_lines
        if _is_import(line, parsed.language)
    ]

    # A signature change is a function that exists on both sides but whose
    # parameter list or return annotation moved.
    removed_by_name = {_def_name(d): d for d in parsed.removed_functions}
    for definition in parsed.added_functions:
        name = _def_name(definition)
        if name in removed_by_name and removed_by_name[name] != definition:
            parsed.changed_signatures.append(
                f"{name}: {removed_by_name[name].strip()} -> {definition.strip()}"
            )


def _is_import(line: str, language: str) -> bool:
    stripped = line.strip()
    if language == "python":
        return stripped.startswith(("import ", "from "))
    if language == "javascript":
        return stripped.startswith("import ") or "require(" in stripped
    return False


def _def_name(definition: str) -> str:
    match = re.search(r"(?:def|function|class)\s+([A-Za-z_][A-Za-z0-9_]*)", definition)
    return match.group(1) if match else definition.strip()


def _regex_defs(text: str) -> list[str]:
    if not text.strip():
        return []
    return re.findall(r"^\s*(?:async\s+)?(?:def|function|class)\s+.+$", text, re.M)


def _top_level_defs(text: str, language: str) -> list[str]:
    """Extract top-level function/class headers using a real AST parse."""
    if not text.strip():
        return []
    try:
        parser = tree_sitter.Parser(_PY_LANGUAGE if language == "python" else _JS_LANGUAGE)
        tree = parser.parse(text.encode())
    except Exception:  # noqa: BLE001
        return _regex_defs(text)

    wanted = (
        {"function_definition", "class_definition"}
        if language == "python"
        else {"function_declaration", "class_declaration", "method_definition"}
    )
    found: list[str] = []

    def walk(node, depth: int = 0) -> None:
        if node.type in wanted and depth <= 2:
            header = text[node.start_byte : node.end_byte].split("\n")[0]
            # Strip a trailing body opener so only the signature is kept.
            found.append(header.rstrip(":").rstrip("{").strip())
        for child in node.children:
            walk(child, depth + 1)

    walk(tree.root_node)
    return found


# ---------------------------------------------------------------------------
# Heuristic mapping from parsed files onto the ontology
# ---------------------------------------------------------------------------

#: Map a source path fragment to the ontology id prefix it belongs to. In a
#: real deployment this comes from the ingestion pipeline that built the graph;
#: here it makes the demo's diffs resolve to real node ids.
PATH_PREFIX_RULES: tuple[tuple[str, str], ...] = (
    ("auth", "AUTH"),
    ("token", "AUTH"),
    ("billing", "BILL"),
    ("order", "ORDER"),
    ("payment", "PAY"),
    ("notification", "NOTIF"),
    ("gateway", "GW"),
    ("redis", "CACHE"),
    ("db", "DB"),
    ("user", "USER"),
)


def _prefix_for(path: str) -> str | None:
    lowered = path.lower()
    for fragment, prefix in PATH_PREFIX_RULES:
        if fragment in lowered:
            return prefix
    return None


def build_mutation_spec(
    diff_text: str,
    pr_id: str,
    graph_modules: Sequence[dict] = (),
    graph_schema_fields: Sequence[dict] = (),
    declare_breaking: bool = False,
    latency_delta_ms: float = 0.0,
) -> tuple[MutationSpec, list[ParsedFile]]:
    """Map a diff onto concrete ontology node ids.

    ``graph_modules`` / ``graph_schema_fields`` are the rows already present in
    the graph, which is what lets the parser attach a diff hunk to a real
    ``ASTModule`` id instead of inventing one.
    """
    parsed = parse_unified_diff(diff_text)
    mutation = MutationSpec(breaking_schema_change=declare_breaking)
    mutation.latency_delta_ms = latency_delta_ms

    module_by_path = {
        row.get("file_path", ""): row["id"]
        for row in graph_modules
        if row.get("file_path")
    }

    matched_modules: list[str] = []
    for file in parsed:
        module_id = module_by_path.get(file.path)
        if module_id is None:
            module_id = _fuzzy_module_match(file.path, graph_modules)
        if module_id:
            matched_modules.append(module_id)

    mutation.modified_module_ids = sorted(set(matched_modules))

    # Schema deltas. A field counts as *removed* only when it is referenced on a
    # removed line and never on an added line - otherwise a reordered payload
    # would look like it dropped every key it still has.
    removed_text = "\n".join(line for file in parsed for line in file.removed_lines)
    added_text = "\n".join(line for file in parsed for line in file.added_lines)
    removed_refs = _field_references(removed_text)
    added_refs = _field_references(added_text)

    breaking_ids: set[str] = set()
    for field in graph_schema_fields:
        field_id = field["id"]
        short = field.get("name") or field_id.split(".")[-1]
        if short in removed_refs and short not in added_refs:
            mutation.removed_schema_field_ids.append(field_id)
            if field.get("breaking"):
                breaking_ids.add(field_id)
        elif short in added_refs and short not in removed_refs:
            mutation.added_schema_field_ids.append(field_id)

    # A removed field the graph already marks as ``breaking`` is a breaking
    # change by definition - the graph knows, so the parser does not guess.
    if breaking_ids:
        mutation.breaking_schema_change = True

    # New imports inside the touched services become IMPORTS edges. They are
    # resolved lazily by the simulator against the shadow graph.
    for file in parsed:
        for import_line in file.added_imports:
            target = _import_target(import_line)
            if target:
                source_module = module_by_path.get(file.path)
                if source_module:
                    mutation.added_import_edges.append((source_module, target))

    # A signature change on an endpoint that carries a payload is breaking even
    # when no field node exists for it yet.
    signature_changes = [
        change for file in parsed for change in file.changed_signatures
    ]
    if signature_changes and any(
        "payload" in change.lower() or "schema" in change.lower()
        for change in signature_changes
    ):
        mutation.breaking_schema_change = True

    mutation.summary = (
        f"{len(parsed)} file(s) parsed, {len(mutation.modified_module_ids)} "
        f"module(s) matched to graph nodes, "
        f"{len(mutation.removed_schema_field_ids)} schema field(s) removed, "
        f"{len(mutation.added_schema_field_ids)} added"
    )
    return mutation, parsed


def _field_references(text: str) -> set[str]:
    """Identifiers that look like payload keys or typed field declarations.

    Function signatures are skipped deliberately: ``def verify(self, token: str)``
    mentions ``token``, but that is a parameter, not a schema field. Without this
    guard the parser reports the auth *login* payload as changed by any diff that
    happens to use the word.
    """
    refs: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _looks_like_signature(stripped):
            continue
        # Quoted dictionary / JSON keys: ``"scope": ...`` or ``'scope': ...``
        refs.update(re.findall(r"[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*:", stripped))
        # Typed field declarations: ``scope: str`` / ``scope: Optional[str]``
        refs.update(
            re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*[A-Za-z_\[]", stripped)
        )
    return refs


def _looks_like_signature(line: str) -> bool:
    return bool(
        re.match(r"^\s*(?:async\s+)?(?:def|function|class)\s", line)
        or re.match(r"^\s*(?:public|private|protected|static|export)\s", line)
    )


def _fuzzy_module_match(path: str, graph_modules: Sequence[dict]) -> str | None:
    prefix = _prefix_for(path)
    if not prefix:
        return None
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    for row in graph_modules:
        candidate = (row.get("file_path") or "").lower()
        if prefix.lower() in candidate and stem and stem in candidate:
            return row["id"]
    for row in graph_modules:
        if prefix.lower() in (row.get("file_path") or "").lower():
            return row["id"]
    return None


def _mentions_identifier(text: str, identifier: str) -> bool:
    if not identifier:
        return False
    return re.search(rf"\b{re.escape(identifier)}\b", text) is not None


def _import_target(import_line: str) -> str | None:
    """Extract a module-ish target from an import statement."""
    stripped = import_line.strip()
    match = re.search(
        r"from\s+([\w\.]+)\s+import|import\s+([\w\.]+)|require\(['\"]([^'\"]+)['\"]\)",
        stripped,
    )
    if not match:
        return None
    target = next((g for g in match.groups() if g), None)
    return target
