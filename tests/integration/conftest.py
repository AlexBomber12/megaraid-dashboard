"""Reuse the isolated application, migrations and fake executable from Playwright."""

from tests.e2e.conftest import (  # noqa: F401
    _e2e_session_env,
    _live_server_handle,
    _reset_auth_rate_limit_attempts,
    fake_storcli,
    fresh_db,
    live_server,
    snapshot_with_drives,
)
