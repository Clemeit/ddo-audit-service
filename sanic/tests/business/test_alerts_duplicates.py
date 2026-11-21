"""Duplicate alert rules: AlertType.criteria_key and the check in services.alerts_db."""

import pytest

import business.alerts  # noqa: F401  (registers the alert types)
import services.alerts_db as alerts_db
from business.alerts import registry


def _key(rule_type, criteria):
    return registry.get_type(rule_type).criteria_key(criteria)


def test_same_criteria_in_a_different_order_match():
    a = {"quest_ids": [1, 2], "server_names": ["Argonnessen", "Cannith"]}
    b = {"quest_ids": [2, 1], "server_names": ["Cannith", "Argonnessen"]}
    assert _key("lfm_posted", a) == _key("lfm_posted", b)


def test_stored_criteria_are_normalised_before_comparing():
    # Older rows may lack fields that now have defaults, or use other server spelling.
    stored = {"server_names": ["argonnessen"]}
    new = {"server_names": ["Argonnessen"], "on_offline": True, "on_online": True}
    assert _key("server_status", stored) == _key("server_status", new)


def test_different_criteria_do_not_match():
    online = {"server_names": [], "on_offline": False, "on_online": True}
    both = {"server_names": [], "on_offline": True, "on_online": True}
    assert _key("server_status", online) != _key("server_status", both)


def test_display_only_fields_are_ignored():
    a = {"item_slug": "some-item", "item_name": "Some Item"}
    b = {"item_slug": "some-item", "item_name": "Renamed Item"}
    assert _key("shard_exchange_listed", a) == _key("shard_exchange_listed", b)


def test_invalid_stored_criteria_still_compare():
    # A row that no longer validates falls back to its raw criteria instead of raising.
    assert _key("lfm_posted", {"quest_ids": []}) == _key("lfm_posted", {"quest_ids": []})


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    async def execute(self, sql, params=None):
        self.params = params

    async def fetchall(self):
        _user_id, rule_type, exclude_id = self.params
        return [r for r in self.rows if r["type"] == rule_type and r["id"] != exclude_id]


def _rule(rule_id, criteria, rule_type="server_status"):
    return {"id": rule_id, "type": rule_type, "criteria": criteria}


ANY_SERVER_ONLINE = {"server_names": [], "on_offline": False, "on_online": True}


def test_raise_if_duplicate_finds_the_matching_rule(run_async):
    cursor = _FakeCursor([_rule(1, {**ANY_SERVER_ONLINE, "on_offline": True}), _rule(2, ANY_SERVER_ONLINE)])
    key = registry.get_type("server_status").criteria_key

    with pytest.raises(alerts_db.DuplicateRule) as exc:
        run_async(alerts_db._raise_if_duplicate(cursor, 1, "server_status", ANY_SERVER_ONLINE, key))

    assert exc.value.rule["id"] == 2


def test_raise_if_duplicate_skips_the_rule_being_edited(run_async):
    cursor = _FakeCursor([_rule(2, ANY_SERVER_ONLINE)])
    key = registry.get_type("server_status").criteria_key

    run_async(alerts_db._raise_if_duplicate(cursor, 1, "server_status", ANY_SERVER_ONLINE, key, 2))
