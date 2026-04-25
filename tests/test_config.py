from pathlib import Path

import pytest
from pydantic import ValidationError

from megaraid_dashboard.config import Settings


def test_admin_credentials_are_required(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)

    with pytest.raises(ValidationError):
        Settings()
