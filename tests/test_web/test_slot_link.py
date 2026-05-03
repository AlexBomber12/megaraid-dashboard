from __future__ import annotations

from megaraid_dashboard.web.templates import slot_link


def test_slot_link_wraps_first_slot_token() -> None:
    rendered = str(slot_link("PD 2:0 degraded"))

    assert rendered == 'PD <a href="/drives/2:0">2:0</a> degraded'


def test_slot_link_wraps_event_detector_slot_token() -> None:
    rendered = str(slot_link("PD e252:s4 state is Failed"))

    assert rendered == 'PD <a href="/drives/252:4">e252:s4</a> state is Failed'


def test_slot_link_uses_supplied_slot_url() -> None:
    rendered = str(
        slot_link("PD e252:s4 state is Failed", slot_url=lambda slot: f"/raid/drives/{slot}")
    )

    assert rendered == 'PD <a href="/raid/drives/252:4">e252:s4</a> state is Failed'


def test_slot_link_returns_escaped_input_when_no_slot_exists() -> None:
    rendered = str(slot_link("Generic message no slot"))

    assert rendered == "Generic message no slot"


def test_slot_link_wraps_only_first_slot_token() -> None:
    rendered = str(slot_link("Two slots 2:0 and 3:1 mentioned"))

    assert rendered == 'Two slots <a href="/drives/2:0">2:0</a> and 3:1 mentioned'


def test_slot_link_escapes_non_slot_text() -> None:
    rendered = str(slot_link("malicious <script> 2:0"))

    assert rendered == 'malicious &lt;script&gt; <a href="/drives/2:0">2:0</a>'
