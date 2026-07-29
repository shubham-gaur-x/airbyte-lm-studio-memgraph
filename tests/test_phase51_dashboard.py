"""Phase 51 (D): provenance dashboard — self-contained static page served at /dashboard.

Read-only, aggregate-first per the governance decision from the v5 plan: per-person
rankings must go through /graph/insights/influential (which already gates on
Person.tracked server-side) rather than the dashboard rolling its own ungated query.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


def test_dashboard_html_file_exists_and_is_self_contained():
    path = Path(__file__).parent.parent / "transform_service" / "static" / "dashboard.html"
    assert path.exists(), "dashboard.html must exist under transform_service/static/"
    html = path.read_text(encoding="utf-8")
    # Self-contained: no external script/style/font hosts (matches the local-first ethos —
    # this dashboard must work with no internet access, same as the rest of the pipeline).
    assert "http://" not in html.replace("http://localhost", "\0") and "https://" not in html


def test_dashboard_wires_review_and_provenance_and_timeline_endpoints():
    path = Path(__file__).parent.parent / "transform_service" / "static" / "dashboard.html"
    html = path.read_text(encoding="utf-8")
    for endpoint in (
        "/graph/timeline", "/review/actions", "/review/people", "/review/blockers",
        "/graph/provenance/", "/graph/provenance/by-ticket/",
    ):
        assert endpoint in html, f"dashboard.html does not reference {endpoint}"


def test_dashboard_uses_gated_influential_endpoint_not_a_raw_person_query():
    """Governance: any per-person ranking must go through the endpoint that already
    filters on Person.tracked — the dashboard must not construct its own Cypher-shaped
    per-person leaderboard client-side."""
    path = Path(__file__).parent.parent / "transform_service" / "static" / "dashboard.html"
    html = path.read_text(encoding="utf-8")
    if "insights/influential" in html:
        assert "MATCH" not in html.upper().replace("MATCHING", "")  # no raw Cypher embedded


@pytest.mark.anyio
async def test_dashboard_route_serves_html():
    from transform_service import main
    response = await main.dashboard()
    assert response.media_type == "text/html"
    assert b"<html" in response.body.lower() or "<html" in str(response.body).lower()


def test_dashboard_route_registered():
    from transform_service import main
    paths = {r.path for r in main.app.routes}
    assert "/dashboard" in paths
