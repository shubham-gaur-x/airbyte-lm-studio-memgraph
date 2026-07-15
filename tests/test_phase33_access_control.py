"""Phase 33 (core): principal→scope policy, AccessDenied, aggregates rule, predicates."""
from __future__ import annotations

import pytest

from transform_service import access_control as ac


class TestParseScope:
    def test_all_and_org(self):
        assert ac.parse_scope("all") == ac.Scope("all")
        assert ac.parse_scope("org") == ac.Scope("org")

    def test_team_and_project(self):
        assert ac.parse_scope("team:QA AI") == ac.Scope("team", "QA AI")
        assert ac.parse_scope("project:SCRUM") == ac.Scope("project", "SCRUM")

    def test_bad_scope_raises(self):
        with pytest.raises(ValueError):
            ac.parse_scope("team:")
        with pytest.raises(ValueError):
            ac.parse_scope("nonsense")


def _policy():
    return {
        "admin-user": ac.Principal("admin-user", ac.ADMIN),
        "lead-user": ac.Principal("lead-user", ac.LEAD, team="QA AI"),
        "member-user": ac.Principal("member-user", ac.MEMBER, team="QA AI"),
    }


class TestAuthorize:
    def test_admin_sees_everything(self):
        p = _policy()
        for scope in ("all", "org", "team:Other", "project:SCRUM"):
            assert ac.authorize("admin-user", scope, p).token() == ac.parse_scope(scope).token()

    def test_member_only_own_team(self):
        p = _policy()
        assert ac.authorize("member-user", "team:QA AI", p) == ac.Scope("team", "QA AI")
        with pytest.raises(ac.AccessDenied):
            ac.authorize("member-user", "team:Other", p)
        with pytest.raises(ac.AccessDenied):
            ac.authorize("member-user", "all", p)

    def test_lead_gets_org_but_not_other_team_detail(self):
        p = _policy()
        assert ac.authorize("lead-user", "org", p) == ac.Scope("org")
        assert ac.authorize("lead-user", "team:QA AI", p).value == "QA AI"
        with pytest.raises(ac.AccessDenied):
            ac.authorize("lead-user", "team:Other", p)

    def test_unknown_principal_denied(self):
        with pytest.raises(ac.AccessDenied):
            ac.authorize("ghost", "org", _policy())

    def test_explicit_allow_list_grants_scope(self):
        p = {"svc": ac.Principal("svc", ac.MEMBER, team="X", allowed_scopes=("project:SCRUM",))}
        assert ac.authorize("svc", "project:SCRUM", p) == ac.Scope("project", "SCRUM")


class TestAggregatesOnly:
    def test_lead_org_query_is_aggregates(self):
        assert ac.aggregates_only("lead-user", "org", _policy()) is True

    def test_admin_org_query_is_detail(self):
        assert ac.aggregates_only("admin-user", "org", _policy()) is False

    def test_team_scope_is_not_aggregate_gated(self):
        assert ac.aggregates_only("member-user", "team:QA AI", _policy()) is False


class TestScopePredicate:
    def test_team_predicate(self):
        assert ac.scope_predicate(ac.Scope("team", "QA AI")) == {"scope_team": "QA AI"}

    def test_project_predicate(self):
        assert ac.scope_predicate(ac.Scope("project", "SCRUM")) == {"scope_project": "SCRUM"}

    def test_org_and_all_have_no_property_filter(self):
        assert ac.scope_predicate(ac.Scope("org")) == {}
        assert ac.scope_predicate(ac.Scope("all")) == {}


class TestVisibleScopesAndDefaultPolicy:
    def test_visible_scopes_by_role(self):
        p = _policy()
        assert ac.visible_scopes("admin-user", p) == ["all", "org"]
        assert "org" in ac.visible_scopes("lead-user", p)
        assert "team:QA AI" in ac.visible_scopes("member-user", p)

    def test_default_policy_loads(self, monkeypatch):
        monkeypatch.delenv("ACCESS_POLICY_FILE", raising=False)
        policy = ac.load_policy()
        assert policy["claude-desktop"].role == ac.ADMIN
        assert policy["dev-agent"].role == ac.MEMBER
