import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_ALERT_RULE_BODY_BYTES = 8 * 1024


class AlertRuleInput(BaseModel):
    """Body of POST/PUT /v1/user/alerts/rules. ``criteria`` is validated by the type's own model."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=64)
    criteria: dict = Field(default_factory=dict)
    enabled: bool = True
    expires_at: Optional[datetime] = None


MAX_ALERT_PREFERENCES_BODY_BYTES = 1024
_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _time_to_minute(value: str) -> int:
    match = _TIME_PATTERN.match(value)
    if not match:
        raise ValueError("must be a time like 07:30")
    return int(match.group(1)) * 60 + int(match.group(2))


def minute_to_time(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


class AlertPreferencesInput(BaseModel):
    """Body of PUT /v1/user/alerts/preferences: the account-wide quiet-hours window.

    Alerts that match while the window is open are skipped, not delayed. The window is in ``timezone``
    (an IANA name) and may cross midnight (e.g. 23:00 -> 07:00).
    """

    model_config = ConfigDict(extra="forbid")

    quiet_enabled: bool = False
    quiet_start: str = Field(default="23:00", max_length=5)
    quiet_end: str = Field(default="07:00", max_length=5)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        _time_to_minute(value)
        return value

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ValueError("must be an IANA time zone such as America/New_York")
        return value

    @model_validator(mode="after")
    def _window_not_empty(self):
        if self.quiet_enabled and self.quiet_start == self.quiet_end:
            raise ValueError("quiet hours need different start and end times")
        return self

    @property
    def start_minute(self) -> int:
        return _time_to_minute(self.quiet_start)

    @property
    def end_minute(self) -> int:
        return _time_to_minute(self.quiet_end)
