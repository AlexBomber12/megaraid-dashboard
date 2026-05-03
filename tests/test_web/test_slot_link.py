from __future__ import annotations

from megaraid_dashboard.web.templates import slot_link


def test_slot_link_wraps_first_slot_token() -> None:
    rendered = str(slot_link("PD 2:0 degraded"))

    assert rendered == 'PD <a href="/drives/2:0">2:0</a> degraded'


def test_slot_link_returns_escaped_input_when_no_slot_exists() -> None:
    rendered = str(slot_link("Generic message no slot"))

    assert rendered == "Generic message no slot"


def test_slot_link_wraps_only_first_slot_token() -> None:
    rendered = str(slot_link("Two slots 2:0 and 3:1 mentioned"))

    assert rendered == 'Two slots <a href="/drives/2:0">2:0</a> and 3:1 mentioned'


def test_slot_link_escapes_non_slot_text() -> None:
    rendered = str(slot_link("malicious <script> 2:0"))

    assert rendered == 'malicious &lt;script&gt; <a href="/drives/2:0">2:0</a>'
