"""Summaries count each presentation grade without hiding unknown entries or showing zero health."""

from pathlib import Path
from types import SimpleNamespace

from megaraid_dashboard.web.templates import create_templates


def test_common_summary_counts_unknown_and_keeps_attention_separate() -> None:
    directory = Path(__file__).resolve().parents[2] / "src/megaraid_dashboard/templates"
    template = create_templates(directory).env.from_string(
        "{% from 'partials/_health.html' import drive_summary %}"
        "{{ drive_summary(items, 'severity') }}"
    )
    html = template.render(
        items=[
            SimpleNamespace(severity=grade)
            for grade in ("critical", "warning", "unknown", "unexpected")
        ]
    )
    assert "1 Critical" in html
    assert "1 Attention" in html
    assert "2 Unknown" in html
    assert "4 Drives" in html
    assert "Healthy" not in html
    assert "status-badge--unknown" in html
    prefixed = (
        create_templates(directory)
        .env.from_string(
            "{% from 'partials/_health.html' import drive_summary %}"
            "{{ drive_summary(items, 'severity', '/raid/drives') }}"
        )
        .render(items=[SimpleNamespace(severity="optimal")])
    )
    assert '<a href="/raid/drives">1 Drives →</a>' in prefixed


def test_common_summary_with_no_drives_does_not_claim_health() -> None:
    directory = Path(__file__).resolve().parents[2] / "src/megaraid_dashboard/templates"
    template = create_templates(directory).env.from_string(
        "{% from 'partials/_health.html' import drive_summary %}"
        "{{ drive_summary(items, 'severity') }}"
    )
    html = template.render(items=[])
    assert "0 Drives" in html
    assert "status-badge--" not in html
