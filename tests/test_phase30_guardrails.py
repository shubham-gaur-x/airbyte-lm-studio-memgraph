"""Phase 30: deterministic review guardrails — one planted violation per gate."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

for mod_name in ("structlog",):
    if mod_name not in sys.modules:
        stub = types.ModuleType(mod_name)
        stub.get_logger = lambda: MagicMock()  # type: ignore[attr-defined]
        sys.modules[mod_name] = stub

from dev_agent import guardrails as g  # noqa: E402


# --- Gate 1/2: tests + lint/type -------------------------------------------
class TestCommandGates:
    def test_tests_green_pass_and_fail(self):
        assert g.gate_tests_green(lambda: (0, "126 passed")).passed
        r = g.gate_tests_green(lambda: (1, "2 failed"))
        assert not r.passed and "failed" in r.evidence

    def test_lint_type_clean_requires_both(self):
        ok = g.gate_lint_type_clean(lambda: (0, ""), lambda: (0, ""))
        assert ok.passed
        bad = g.gate_lint_type_clean(lambda: (1, "E501 line too long"), lambda: (0, ""))
        assert not bad.passed and "ruff" in bad.evidence


# --- Gate 3: diff budget ----------------------------------------------------
class TestDiffBudget:
    def test_within_budget(self):
        assert g.gate_diff_budget(["a.py", "b.py"], 120).passed

    def test_too_many_lines(self):
        assert not g.gate_diff_budget(["a.py"], 999).passed

    def test_too_many_files(self):
        assert not g.gate_diff_budget([f"f{i}.py" for i in range(11)], 10).passed


# --- Gate 4: protected paths ------------------------------------------------
class TestProtectedPaths:
    def test_clean_diff(self):
        assert g.gate_protected_paths(["transform_service/main.py", "tests/test_x.py"]).passed

    def test_env_file_blocked(self):
        assert not g.gate_protected_paths([".env"]).passed

    def test_env_example_allowed(self):
        assert g.gate_protected_paths([".env.example"]).passed

    def test_workflow_and_escape_blocked(self):
        assert not g.gate_protected_paths([".github/workflows/ci.yml"]).passed
        assert not g.gate_protected_paths(["../other-repo/file.py"]).passed


# --- Gate 5: no new deps ----------------------------------------------------
class TestNoNewDeps:
    def test_no_dep_change(self):
        assert g.gate_no_new_deps(["transform_service/main.py"], "do a thing").passed

    def test_dep_change_without_opt_in_fails(self):
        assert not g.gate_no_new_deps(["transform_service/requirements.txt"], "do a thing").passed

    def test_dep_change_opted_in_and_pinned(self):
        r = g.gate_no_new_deps(
            ["transform_service/requirements.txt"],
            "add feature. allow-new-dependency",
            added_dep_lines=["tenacity==8.2.3"],
        )
        assert r.passed

    def test_dep_change_opted_in_but_unpinned_fails(self):
        r = g.gate_no_new_deps(
            ["transform_service/requirements.txt"],
            "allow-new-dependency",
            added_dep_lines=["tenacity"],
        )
        assert not r.passed and "unpinned" in r.evidence


# --- Gate 6: secret scan ----------------------------------------------------
class TestSecretScan:
    def test_clean(self):
        assert g.gate_secret_scan(["x = compute()", "return {'ok': True}"]).passed

    def test_planted_openai_key(self):
        assert not g.gate_secret_scan(["KEY = 'sk-abcdef0123456789abcdef'"]).passed

    def test_planted_github_pat(self):
        assert not g.gate_secret_scan(["url = 'github_pat_11ABCDE0000fghijklmnop'"]).passed

    def test_planted_assignment(self):
        assert not g.gate_secret_scan(["password = \"hunter2secret\""]).passed


# --- Gate 7: module boundaries ---------------------------------------------
class TestModuleBoundaries:
    def test_cypher_in_memgraph_client_ok(self):
        contents = {"transform_service/memgraph_client.py": 'q = "MERGE (m:Meeting {id: $id}) RETURN m"'}
        assert g.gate_module_boundaries(contents).passed

    def test_cypher_in_wrong_module_flagged(self):
        contents = {"transform_service/graph_builder.py": 'q = "MERGE (m:Meeting {id: $id}) RETURN m"'}
        r = g.gate_module_boundaries(contents)
        assert not r.passed and "Cypher" in r.evidence

    def test_sql_outside_db_flagged(self):
        contents = {"transform_service/main.py": 'q = "SELECT * FROM raw_emails"'}
        assert not g.gate_module_boundaries(contents).passed

    def test_sql_in_db_ok(self):
        contents = {"transform_service/db.py": 'q = "SELECT * FROM raw_emails"'}
        assert g.gate_module_boundaries(contents).passed

    def test_mage_call_outside_graph_algorithms_flagged(self):
        contents = {"transform_service/main.py": 'q = "CALL pagerank.get() YIELD node"'}
        assert not g.gate_module_boundaries(contents).passed

    def test_jira_rest_outside_jira_client_flagged(self):
        contents = {"dev_agent/orchestrator.py": 'url = "https://x.atlassian.net/rest/api/3/issue"'}
        assert not g.gate_module_boundaries(contents).passed

    def test_airbyte_sdk_import_outside_action_agent_flagged(self):
        contents = {"transform_service/graph_builder.py": "from airbyte_agent import JiraConnector\n"}
        assert not g.gate_module_boundaries(contents).passed

    def test_ordinary_identifier_not_flagged(self):
        # 'select' as a variable name must NOT trip the SQL gate (string/comment-only match).
        contents = {"transform_service/main.py": "select = choose_option()\nmatch = re.match(p, s)\n"}
        assert g.gate_module_boundaries(contents).passed


# --- Aggregate + verdict models --------------------------------------------
class TestAggregateAndModels:
    def test_all_passed_and_failed_gates(self):
        results = [
            g.GateResult(name="a", passed=True, evidence=""),
            g.GateResult(name="b", passed=False, evidence="boom"),
        ]
        assert not g.all_passed(results)
        assert [r.name for r in g.failed_gates(results)] == ["b"]

    def test_review_verdict_parses(self):
        v = g.ReviewVerdict(
            verdict="request_changes",
            findings=[{"severity": "high", "file": "main.py", "issue": "no error handling"}],
        )
        assert v.verdict == "request_changes"
        assert v.findings[0].severity == "high"
