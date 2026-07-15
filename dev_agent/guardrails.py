"""Phase 30 — review guardrails for the dev agent.

Two layers protect every PR the agent opens:

1. Seven DETERMINISTIC gates (this module). Each is a pure function returning a
   ``GateResult`` so it is trivially unit-testable with planted violations. The thin
   ``run_deterministic_gates`` wrapper gathers the real diff/command output and calls them.
2. An independent LLM reviewer (``review_models`` + the reviewer prompt) that gets the
   ticket, spec, diff, and gate evidence and returns a strict JSON verdict.

The agent NEVER merges its own PR and NEVER bypasses a gate — merging stays human.
"""
from __future__ import annotations

import ast
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class GateResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    passed: bool
    evidence: str


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    severity: str  # "low" | "medium" | "high"
    file: str = ""
    issue: str
    suggested_fix: str = ""


class ReviewVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdict: str  # "approve" | "request_changes"
    findings: List[ReviewFinding] = []


# ---------------------------------------------------------------------------
# Gate 1/2 — tests + lint/type (command runners injected for testability)
# ---------------------------------------------------------------------------

# A command runner returns (exit_code, combined_output).
CommandRunner = Callable[[], Tuple[int, str]]


def gate_tests_green(runner: CommandRunner) -> GateResult:
    code, output = runner()
    return GateResult(
        name="tests_green",
        passed=code == 0,
        evidence=(output or "").strip()[-800:] or ("passed" if code == 0 else "failed"),
    )


def gate_lint_type_clean(lint: CommandRunner, typecheck: CommandRunner) -> GateResult:
    lc, lo = lint()
    tc, to = typecheck()
    passed = lc == 0 and tc == 0
    parts = []
    if lc != 0:
        parts.append(f"ruff: {lo.strip()[-300:]}")
    if tc != 0:
        parts.append(f"mypy: {to.strip()[-300:]}")
    return GateResult(
        name="lint_type_clean",
        passed=passed,
        evidence="clean" if passed else " | ".join(parts),
    )


# ---------------------------------------------------------------------------
# Gate 3 — diff budget
# ---------------------------------------------------------------------------


def gate_diff_budget(
    changed_files: Sequence[str],
    changed_lines: int,
    max_files: int = 10,
    max_lines: int = 600,
) -> GateResult:
    n_files = len(changed_files)
    ok = n_files <= max_files and changed_lines <= max_lines
    return GateResult(
        name="diff_budget",
        passed=ok,
        evidence=f"{n_files} files (max {max_files}), {changed_lines} lines (max {max_lines})",
    )


# ---------------------------------------------------------------------------
# Gate 4 — protected paths
# ---------------------------------------------------------------------------

_PROTECTED_PATTERNS = [
    re.compile(r"(^|/)\.env"),                 # .env, .env.*  (secrets)
    re.compile(r"(^|/)\.github/workflows/"),   # CI config
    re.compile(r"(^|/)(secrets?|credentials?)(/|\.|$)", re.IGNORECASE),
    re.compile(r"\.pem$|\.key$|id_rsa"),       # key material
]


def gate_protected_paths(changed_files: Sequence[str]) -> GateResult:
    """Fail if the diff touches secrets, CI, key material, or anything outside the repo.

    ``.env.example`` is allowed (it holds no secrets); everything matching ``.env`` else
    is blocked. Paths that escape the repo root (``..`` or absolute) always fail.
    """
    violations: List[str] = []
    for f in changed_files:
        norm = f.replace("\\", "/")
        if norm.startswith("/") or ".." in norm.split("/"):
            violations.append(f"{f} (outside repo)")
            continue
        if norm.endswith(".env.example"):
            continue
        for pat in _PROTECTED_PATTERNS:
            if pat.search(norm):
                violations.append(f)
                break
    return GateResult(
        name="protected_paths",
        passed=not violations,
        evidence="none" if not violations else "touched: " + ", ".join(sorted(set(violations))),
    )


# ---------------------------------------------------------------------------
# Gate 5 — no new dependencies (unless explicitly allowed)
# ---------------------------------------------------------------------------

_DEP_FILES = ("requirements.txt", "requirements.in", "poetry.lock", "Pipfile.lock", "pyproject.toml")
_ALLOW_TOKEN = "allow-new-dependency"


def gate_no_new_deps(
    changed_files: Sequence[str],
    ticket_description: str,
    added_dep_lines: Optional[Sequence[str]] = None,
) -> GateResult:
    """Dependency/lock files must be unchanged unless the ticket opts in.

    If opted in, any added requirement line must be version-pinned (``==`` / ``@`` / ``>=``).
    """
    touched = [f for f in changed_files if any(f.replace("\\", "/").endswith(d) for d in _DEP_FILES)]
    if not touched:
        return GateResult(name="no_new_deps", passed=True, evidence="no dependency files changed")
    if _ALLOW_TOKEN not in (ticket_description or ""):
        return GateResult(
            name="no_new_deps",
            passed=False,
            evidence=f"changed {touched} without '{_ALLOW_TOKEN}' in ticket",
        )
    unpinned = [
        ln.strip()
        for ln in (added_dep_lines or [])
        if ln.strip() and not ln.lstrip().startswith("#") and not re.search(r"(==|>=|~=|@)", ln)
    ]
    if unpinned:
        return GateResult(
            name="no_new_deps",
            passed=False,
            evidence=f"allowed but unpinned: {unpinned}",
        )
    return GateResult(name="no_new_deps", passed=True, evidence=f"allowed + pinned ({touched})")


# ---------------------------------------------------------------------------
# Gate 6 — secret scan
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),                       # OpenAI/Anthropic-style
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),              # GitHub PAT
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),                      # GitHub classic token
    re.compile(r"AKIA[0-9A-Z]{16}"),                          # AWS access key id
    re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
    re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----"),
]


def gate_secret_scan(added_lines: Sequence[str]) -> GateResult:
    hits: List[str] = []
    for ln in added_lines:
        for pat in _SECRET_PATTERNS:
            if pat.search(ln):
                hits.append(ln.strip()[:80])
                break
    return GateResult(
        name="secret_scan",
        passed=not hits,
        evidence="clean" if not hits else f"{len(hits)} suspected secret(s)",
    )


# ---------------------------------------------------------------------------
# Gate 7 — module boundaries (codifies CLAUDE.md conventions as an enforced gate)
# ---------------------------------------------------------------------------

# marker regex -> (human name, set of basenames allowed to contain it)
_BOUNDARY_RULES: List[Tuple[re.Pattern, str, set]] = [
    (re.compile(r"\bCALL\s+[a-z_]+\.[a-z_]+", re.IGNORECASE), "MAGE CALL", {"graph_algorithms.py"}),
    (re.compile(r"\b(MERGE|MATCH)\b.*\b(RETURN|SET|CREATE|DELETE)\b|\bMERGE\s*\("), "Cypher", {"memgraph_client.py"}),
    (re.compile(r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE)\b", re.IGNORECASE), "SQL", {"db.py"}),
    (re.compile(r"/rest/api/|atlassian\.net|/rest/agile/"), "Jira REST", {"jira_client.py"}),
    (re.compile(r"\bimport\s+airbyte|from\s+airbyte"), "airbyte-agent-sdk", {"action_agent.py"}),
]


def gate_module_boundaries(file_contents: Dict[str, str]) -> GateResult:
    """Flag boundary-marker strings that appear in a file not allowed to hold them.

    Only string/comment literals are considered for the Cypher/SQL/Jira markers (via a
    light AST walk) so ordinary identifiers never trip the gate; import markers are matched
    on raw source. Keys of ``file_contents`` are repo-relative paths.
    """
    violations: List[str] = []
    for path, content in file_contents.items():
        base = path.replace("\\", "/").split("/")[-1]
        haystacks = _string_and_comment_text(content)
        raw = content
        for pat, label, allowed in _BOUNDARY_RULES:
            if base in allowed:
                continue
            target = raw if label == "airbyte-agent-sdk" else haystacks
            if pat.search(target):
                violations.append(f"{label} in {path} (allowed only in {sorted(allowed)})")
    return GateResult(
        name="module_boundaries",
        passed=not violations,
        evidence="clean" if not violations else "; ".join(violations),
    )


def _string_and_comment_text(source: str) -> str:
    """Concatenate string literals (best-effort AST) so markers in code strings are seen.

    Falls back to raw source if the file does not parse (a partial agent edit), which is
    the safe direction — better to over-flag than to miss a boundary violation.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    chunks: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            chunks.append(node.value)
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def all_passed(results: Sequence[GateResult]) -> bool:
    return all(r.passed for r in results)


def failed_gates(results: Sequence[GateResult]) -> List[GateResult]:
    return [r for r in results if not r.passed]
