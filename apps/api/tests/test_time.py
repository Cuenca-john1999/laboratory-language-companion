from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from llc_api.core.config import Settings
from llc_api.core.time import local_date


def test_plan_date_uses_configured_civil_timezone():
    instant = datetime(2026, 1, 1, 1, 30, tzinfo=UTC)
    assert local_date(instant, "Europe/Berlin").isoformat() == "2026-01-01"
    assert local_date(instant, "America/Los_Angeles").isoformat() == "2025-12-31"


def test_invalid_timezone_is_rejected_at_configuration_boundary():
    with pytest.raises(ValidationError, match="valid IANA timezone"):
        Settings(timezone="Mars/Olympus_Mons")


def test_local_date_rejects_naive_instants():
    with pytest.raises(ValueError, match="timezone"):
        local_date(datetime(2026, 1, 1), "Europe/Berlin")
