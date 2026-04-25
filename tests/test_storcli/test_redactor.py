from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from tests.fixtures.storcli import redact

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "storcli" / "redacted"


def test_redactor_is_idempotent_on_redacted_fixtures(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    shutil.copytree(FIXTURE_DIR, source_dir)

    redact.redact_directory(source_dir, first_dir)
    redact.redact_directory(first_dir, second_dir)

    for first_path in sorted(first_dir.glob("*.json")):
        second_path = second_dir / first_path.name
        assert second_path.read_text(encoding="utf-8") == first_path.read_text(encoding="utf-8")


def test_redactor_replaces_known_sensitive_shapes(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {
                    "Basics": {
                        "Serial Number": "SVTEST1234",
                        "SAS Address": "500605b000000001",
                        "Host Name": "storage-host",
                    },
                    "Version": {"Firmware Version": "2.05.00.00-0010"},
                    "Drive /c0/e252/s0": [{"EID:Slt": "252:0"}],
                    "Drive /c0/e252/s0 - Detailed Information": {
                        "Drive /c0/e252/s0 Device attributes": {
                            "SN": "     WD-TESTSERIAL",
                            "WWN": "50014ee000000001",
                        },
                        "Drive /c0/e252/s0 Policies/Settings": {
                            "Port Information": [{"SAS address": "0x4433221103000000"}]
                        },
                        "Inquiry Data": "serial encoded here",
                    },
                    "Remote Address": "192.168.50.2",
                },
            }
        ]
    }
    (source_dir / "sample.json").write_text(json.dumps(payload), encoding="utf-8")

    redact.redact_directory(source_dir, output_dir)

    redacted = (output_dir / "sample.json").read_text(encoding="utf-8")
    assert "SV00000001" in redacted
    assert "WD-WM00000001" in redacted
    assert "5000000000000000" in redacted
    assert "0.0.0.0" in redacted
    assert "redacted-host" in redacted
    assert "SVTEST1234" not in redacted
    assert "WD-TESTSERIAL" not in redacted
    assert "500605b000000001" not in redacted
    assert "192.168.50.2" not in redacted
    assert "storage-host" not in redacted
    assert "2.05.00.00-0010" in redacted
