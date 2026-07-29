"""Phase 32: expressive graph — ticket-key extraction (MENTIONS source) regex."""
from __future__ import annotations

from transform_service.utils import extract_ticket_keys


class TestExtractTicketKeys:
    def test_finds_single_key(self):
        assert extract_ticket_keys("Discussed SCRUM-47 with the team") == ["SCRUM-47"]

    def test_multiple_keys_deduped_order_preserving(self):
        text = "SCRUM-47 blocks PROJ-3, and SCRUM-47 again; also PROJ-3."
        assert extract_ticket_keys(text) == ["SCRUM-47", "PROJ-3"]

    def test_no_keys(self):
        assert extract_ticket_keys("just a normal sentence, no tickets") == []
        assert extract_ticket_keys("") == []
        assert extract_ticket_keys(None) == []

    def test_does_not_match_lowercase_or_partial(self):
        # lowercase project, or bare numbers, must not match
        assert extract_ticket_keys("scrum-47 and version 3-2") == []

    def test_requires_two_leading_letters(self):
        # single-letter prefix should not be treated as a Jira key
        assert extract_ticket_keys("A-1 is not a ticket but AB-1 is") == ["AB-1"]

    def test_alphanumeric_project_key(self):
        assert extract_ticket_keys("ticket ABC2-100 shipped") == ["ABC2-100"]
