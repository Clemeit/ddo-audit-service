import re
from datetime import datetime, timedelta, timezone

import pytest

from utils.time import (
    datetime_to_datetime_string,
    get_current_datetime_string,
    timestamp_to_datetime_string,
)


class TestGetCurrentDatetimeString:
    def test_returns_utc_zulu_format(self):
        value = get_current_datetime_string()

        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", value)
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        assert isinstance(parsed, datetime)


class TestDatetimeToDatetimeString:
    def test_naive_datetime_is_treated_as_utc(self):
        value = datetime_to_datetime_string(datetime(2026, 1, 2, 3, 4, 5))
        assert value == "2026-01-02T03:04:05Z"

    def test_timezone_aware_datetime_is_formatted(self):
        aware = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=-5)))
        value = datetime_to_datetime_string(aware)
        assert value == "2026-01-02T03:04:05Z"


class TestTimestampToDatetimeString:
    def test_epoch_zero(self):
        assert timestamp_to_datetime_string(0) == "1970-01-01T00:00:00Z"

    def test_negative_timestamp(self):
        assert timestamp_to_datetime_string(-1) == "1969-12-31T23:59:59Z"

    def test_invalid_type_raises(self):
        with pytest.raises(TypeError):
            timestamp_to_datetime_string("bad")
