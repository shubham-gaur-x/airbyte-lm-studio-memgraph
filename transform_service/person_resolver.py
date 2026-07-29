"""P3 entity resolution: resolve extracted attendees to canonical people.

Two tiers, run BEFORE ``memgraph_client.upsert_meeting_graph`` writes:
  1. Deterministic — normalize the email (fixes the duplicate-Person / duplicate-PageRank
     bug where any variant created a distinct node) and match against a synced roster
     (primary email + aliases).
  2. Probabilistic — fuzzy-match names that miss tier 1 against roster + existing Person
     nodes; below threshold, route to a review queue instead of auto-creating a node.

The no-email case is handled explicitly: hold for review, never silently drop (the old
``if not attendee.email: continue``). ``tracked`` is an opt-in gate (default False) that
per-person analytics must respect. This module issues NO Cypher — the caller supplies
known people via ``memgraph_client.get_known_people``.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

import structlog

log = structlog.get_logger()

FUZZY_THRESHOLD = 0.85


def normalize_email(email: Optional[str]) -> str:
    """Lowercase, trim, and drop any ``+tag`` from the local part."""
    e = (email or "").strip().lower()
    if "@" not in e:
        return e
    local, _, domain = e.partition("@")
    local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def _norm_name(n: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (n or "").strip().lower())


def _name_sim(a: str, b: str) -> float:
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


@dataclass
class RosterEntry:
    name: str
    email: str
    aliases: List[str] = field(default_factory=list)
    tracked: bool = False


@dataclass
class Resolution:
    name: str
    role: str = "attendee"
    email: Optional[str] = None       # canonical email if resolved, else None
    status: str = "resolved"          # "resolved" | "review"
    tracked: bool = False
    reason: str = ""


class Roster:
    def __init__(self, entries: List[RosterEntry]):
        self.entries = entries
        self._by_email: Dict[str, RosterEntry] = {}
        for e in entries:
            self._by_email[normalize_email(e.email)] = e
            for alias in e.aliases:
                self._by_email[normalize_email(alias)] = e

    def match_email(self, email: str) -> Optional[RosterEntry]:
        return self._by_email.get(normalize_email(email))

    def match_name(self, name: str, threshold: float = FUZZY_THRESHOLD) -> Tuple[Optional[RosterEntry], float]:
        best, best_score = None, 0.0
        for e in self.entries:
            s = _name_sim(name, e.name)
            if s > best_score:
                best, best_score = e, s
        return (best, best_score) if best and best_score >= threshold else (None, best_score)


def load_roster() -> Roster:
    """Load the canonical roster from PERSON_ROSTER_PATH (JSON list), or an empty roster."""
    path = os.environ.get("PERSON_ROSTER_PATH", "").strip()
    if not path or not os.path.exists(path):
        return Roster([])
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except Exception as exc:
        log.warning("person_resolver.roster_load_failed", path=path, error=str(exc))
        return Roster([])
    return Roster([
        RosterEntry(
            name=d.get("name", ""),
            email=normalize_email(d.get("email", "")),
            aliases=[normalize_email(a) for a in d.get("aliases", [])],
            tracked=bool(d.get("tracked", False)),
        )
        for d in data if d.get("email")
    ])


def resolve(
    attendee: Any,
    roster: Roster,
    known_people: Optional[List[Dict[str, str]]] = None,
    threshold: float = FUZZY_THRESHOLD,
) -> Resolution:
    """Resolve one attendee (anything with .name/.email/.role) to a canonical Resolution."""
    known_people = known_people or []
    name = getattr(attendee, "name", "") or ""
    role = getattr(attendee, "role", "attendee") or "attendee"
    email = getattr(attendee, "email", None)

    # Tier 1 — deterministic (email present)
    if email and "@" in email:
        ne = normalize_email(email)
        entry = roster.match_email(ne)
        if entry:
            return Resolution(entry.name or name, role, entry.email, "resolved", entry.tracked, "roster-email")
        # Real email, not in roster → canonical is the normalized email (a new person).
        return Resolution(name, role, ne, "resolved", False, "email-normalized")

    # Tier 2 — probabilistic (no email): fuzzy name against roster, then known Person nodes.
    entry, score = roster.match_name(name, threshold)
    if entry:
        return Resolution(entry.name, role, entry.email, "resolved", entry.tracked, f"roster-name:{score:.2f}")

    best = (None, None, 0.0)  # (email, name, score)
    for p in known_people:
        s = _name_sim(name, p.get("name", ""))
        if s > best[2]:
            best = (p.get("email"), p.get("name"), s)
    if best[0] and best[2] >= threshold:
        return Resolution(best[1] or name, role, best[0], "resolved", bool(p.get("tracked", False)), f"person-name:{best[2]:.2f}")

    # Give up → review. Never silently drop.
    return Resolution(name, role, None, "review", False, "no-email-no-match" if not email else "unresolved")


def resolve_attendees(
    attendees: List[Any],
    roster: Roster,
    known_people: Optional[List[Dict[str, str]]] = None,
) -> Tuple[List[Resolution], List[Resolution]]:
    """Return (resolved, reviews) for a list of attendees."""
    resolved: List[Resolution] = []
    reviews: List[Resolution] = []
    for a in attendees:
        r = resolve(a, roster, known_people=known_people)
        (resolved if r.status == "resolved" else reviews).append(r)
    return resolved, reviews
