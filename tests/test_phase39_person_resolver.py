"""Phase 39 (P3a): entity resolution — the pure person_resolver core."""
from __future__ import annotations

from transform_service import person_resolver as pr
from transform_service.models import Attendee


def _roster():
    return pr.Roster([
        pr.RosterEntry(name="Matteo Vaiente", email="matteo@onixnet.com",
                       aliases=["m.vaiente@onixnet.com"], tracked=True),
        pr.RosterEntry(name="Shubham Gaur", email="shubham.gaur@onixnet.com", tracked=False),
    ])


# --- normalize_email -------------------------------------------------------
def test_normalize_email_lowercases_trims_and_strips_plus_tag():
    assert pr.normalize_email("  Matteo+meetings@Onixnet.com ") == "matteo@onixnet.com"
    assert pr.normalize_email("no-at-sign") == "no-at-sign"
    assert pr.normalize_email(None) == ""


def test_email_variants_collapse_to_one_canonical():
    a = pr.normalize_email("Matteo@onixnet.com")
    b = pr.normalize_email("matteo+standup@onixnet.com")
    assert a == b  # the duplicate-Person bug is fixed by normalization


# --- roster matching -------------------------------------------------------
def test_roster_matches_primary_and_alias():
    r = _roster()
    assert r.match_email("MATTEO@onixnet.com").name == "Matteo Vaiente"
    assert r.match_email("m.vaiente@onixnet.com").name == "Matteo Vaiente"
    assert r.match_email("stranger@x.com") is None


def test_roster_name_fuzzy_match():
    r = _roster()
    entry, score = r.match_name("Mateo Vaiente")  # typo
    assert entry is not None and entry.email == "matteo@onixnet.com"
    entry2, _ = r.match_name("Completely Different")
    assert entry2 is None


# --- resolve: tier 1 deterministic ----------------------------------------
def test_resolve_email_in_roster_is_tracked():
    res = pr.resolve(Attendee(name="M", email="matteo+x@onixnet.com"), _roster())
    assert res.status == "resolved" and res.email == "matteo@onixnet.com" and res.tracked is True


def test_resolve_email_not_in_roster_normalizes_untracked():
    res = pr.resolve(Attendee(name="New Person", email="New.Person@corp.com"), _roster())
    assert res.status == "resolved" and res.email == "new.person@corp.com" and res.tracked is False


# --- resolve: tier 2 probabilistic ----------------------------------------
def test_resolve_no_email_fuzzy_matches_roster_name():
    res = pr.resolve(Attendee(name="Matteo Vaiente", email=None), _roster())
    assert res.status == "resolved" and res.email == "matteo@onixnet.com"


def test_resolve_no_email_matches_known_person():
    res = pr.resolve(
        Attendee(name="Alice Smith", email=None), pr.Roster([]),
        known_people=[{"email": "alice@corp.com", "name": "Alice Smith"}],
    )
    assert res.status == "resolved" and res.email == "alice@corp.com"


def test_resolve_no_email_no_match_goes_to_review_not_dropped():
    res = pr.resolve(Attendee(name="Unknown Ghost", email=None), pr.Roster([]))
    assert res.status == "review" and res.email is None and res.reason == "no-email-no-match"


# --- resolve_attendees split ----------------------------------------------
def test_resolve_attendees_splits_resolved_and_review():
    attendees = [
        Attendee(name="Matteo", email="matteo@onixnet.com"),
        Attendee(name="Ghost", email=None),
    ]
    resolved, reviews = pr.resolve_attendees(attendees, _roster())
    assert len(resolved) == 1 and len(reviews) == 1
    assert resolved[0].tracked is True
    assert reviews[0].status == "review"


def test_empty_roster_still_normalizes_and_reviews():
    r = pr.Roster([])
    ok = pr.resolve(Attendee(name="X", email="X+y@Z.com"), r)
    assert ok.email == "x@z.com"
    hold = pr.resolve(Attendee(name="No Email", email=None), r)
    assert hold.status == "review"
