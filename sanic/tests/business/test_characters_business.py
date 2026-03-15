import business.characters as characters_business
from constants.activity import CharacterActivityType
from models.api import CharacterRequestApiModel, CharacterRequestType
from tests.conftest import _amock
from models.character import Character, CharacterClass


def _character(
    character_id: int,
    *,
    server_name: str,
    name: str = "Character",
    location_id: int | None = None,
    guild_name: str | None = None,
    total_level: int | None = None,
    classes: list[CharacterClass] | None = None,
    group_id: int | None = None,
) -> Character:
    return Character(
        id=character_id,
        name=name,
        server_name=server_name,
        location_id=location_id,
        guild_name=guild_name,
        total_level=total_level,
        classes=classes,
        group_id=group_id,
    )


def _activity_type_value(activity_event: dict) -> str:
    activity_type = activity_event["activity_type"]
    return activity_type.value if hasattr(activity_type, "value") else activity_type


def test_persist_deleted_characters_to_db_noop_for_empty_input(monkeypatch, run_async):
    calls = []
    monkeypatch.setattr(
        characters_business.postgres_client,
        "async_add_or_update_characters",
        _amock(lambda characters: calls.append(characters)),
    )

    run_async(characters_business.persist_deleted_characters_to_db([]))

    assert calls == []


def test_persist_deleted_characters_to_db_delegates_to_postgres(monkeypatch, run_async):
    captured = {}
    payload = [{"id": 1, "name": "Persisted"}]

    monkeypatch.setattr(
        characters_business.postgres_client,
        "async_add_or_update_characters",
        _amock(lambda characters: captured.setdefault("characters", characters)),
    )

    run_async(characters_business.persist_deleted_characters_to_db(payload))

    assert captured["characters"] == payload


def test_persist_deleted_characters_to_db_swallows_database_errors(
    monkeypatch, run_async
):
    printed = []

    def _raise_error(_characters):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(
        characters_business.postgres_client,
        "async_add_or_update_characters",
        _amock(_raise_error),
    )
    monkeypatch.setattr("builtins.print", lambda message: printed.append(message))

    run_async(characters_business.persist_deleted_characters_to_db([{"id": 1}]))

    assert len(printed) == 1
    assert "Error persisting characters to database" in printed[0]


def test_persist_character_activity_to_db_delegates_to_postgres(monkeypatch, run_async):
    captured = {}
    activity_payload = [{"character_id": 1, "activity_type": "status", "data": {}}]

    monkeypatch.setattr(
        characters_business.postgres_client,
        "async_add_character_activity",
        _amock(lambda activities: captured.setdefault("activities", activities)),
    )

    run_async(characters_business.persist_character_activity_to_db(activity_payload))

    assert captured["activities"] == activity_payload


def test_aggregate_character_activity_for_server_collects_status_and_field_changes():
    previous_characters = {
        10: {
            "location_id": 100,
            "guild_name": "Old Guild",
            "total_level": 20,
            "classes": [{"name": "Fighter", "level": 20}],
            "group_id": 42,
        },
        30: None,
    }
    current_characters = {
        10: {
            "location_id": 101,
            "guild_name": "New Guild",
            "total_level": 21,
            "classes": [{"name": "Fighter", "level": 21}],
            "group_id": 84,
        },
        20: {"location_id": 555},
        30: {"location_id": 777},
    }

    activity = characters_business.aggregate_character_activity_for_server(
        previous_characters=previous_characters,
        current_characters=current_characters,
        previous_character_ids={10, 30},
        current_character_ids={10, 20, 30},
        deleted_character_ids={11},
    )

    events_by_key = {
        (event["character_id"], _activity_type_value(event)): event
        for event in activity
    }

    assert len(activity) == 6
    assert events_by_key[(11, CharacterActivityType.STATUS.value)]["data"] == {
        "value": False
    }
    assert events_by_key[(20, CharacterActivityType.STATUS.value)]["data"] == {
        "value": True
    }
    assert events_by_key[(10, CharacterActivityType.LOCATION.value)]["data"] == {
        "value": 101
    }
    assert events_by_key[(10, CharacterActivityType.GUILD_NAME.value)]["data"] == {
        "value": "New Guild"
    }
    assert events_by_key[(10, CharacterActivityType.TOTAL_LEVEL.value)]["data"] == {
        "total_level": 21,
        "classes": [{"name": "Fighter", "level": 21}],
    }
    assert events_by_key[(10, CharacterActivityType.GROUP_ID.value)]["data"] == {
        "value": 84
    }


def test_aggregate_character_activity_for_server_logs_failed_character_processing(
    monkeypatch,
):
    log_calls = []
    printed = []

    monkeypatch.setattr(
        characters_business,
        "logMessage",
        lambda **kwargs: log_calls.append(kwargs),
    )
    monkeypatch.setattr("builtins.print", lambda message: printed.append(message))

    activity = characters_business.aggregate_character_activity_for_server(
        previous_characters={1: {"location_id": 1}},
        current_characters={1: None},
        previous_character_ids={1},
        current_character_ids={1},
        deleted_character_ids=set(),
    )

    assert activity == []
    assert len(log_calls) == 1
    assert log_calls[0]["action"] == "aggregate_character_activity_for_server"
    assert log_calls[0]["metadata"]["failed_count"] == 1
    assert any("Error processing character 1" in line for line in printed)
    assert any("failed activity check" in line for line in printed)


def test_handle_incoming_characters_set_filters_server_and_sets_cache(
    monkeypatch, run_async
):
    now = "2026-03-15T12:00:00Z"
    set_calls = []
    update_calls = []
    delete_calls = []
    aggregate_calls = []
    persisted_deleted_calls = []
    persisted_activity_calls = []

    monkeypatch.setattr(
        characters_business, "SERVER_NAMES_LOWERCASE", ["alpha", "beta"]
    )
    monkeypatch.setattr(characters_business, "get_current_datetime_string", lambda: now)
    monkeypatch.setattr(
        characters_business.redis_client,
        "get_characters_by_server_name_as_dict",
        lambda _server_name: {},
    )

    def _aggregate(
        previous_characters,
        incoming_characters,
        previous_character_ids,
        incoming_character_ids,
        deleted_character_ids,
    ):
        aggregate_calls.append(
            {
                "incoming_ids": incoming_character_ids,
                "deleted_ids": deleted_character_ids,
            }
        )
        return []

    monkeypatch.setattr(
        characters_business,
        "aggregate_character_activity_for_server",
        _aggregate,
    )
    monkeypatch.setattr(
        characters_business.redis_client,
        "set_characters_by_server_name",
        lambda payload, server_name: set_calls.append((payload, server_name)),
    )
    monkeypatch.setattr(
        characters_business.redis_client,
        "update_characters_by_server_name",
        lambda payload, server_name: update_calls.append((payload, server_name)),
    )
    monkeypatch.setattr(
        characters_business.redis_client,
        "delete_characters_by_id_and_server_name",
        lambda ids, server_name: delete_calls.append((ids, server_name)),
    )
    monkeypatch.setattr(
        characters_business,
        "persist_deleted_characters_to_db",
        _amock(lambda characters: persisted_deleted_calls.append(characters)),
    )
    monkeypatch.setattr(
        characters_business,
        "persist_character_activity_to_db",
        _amock(
            lambda activity_events: persisted_activity_calls.append(activity_events)
        ),
    )

    alpha_character = _character(1, server_name="Alpha", name="Alpha One")
    invalid_character = _character(999, server_name="Unknown", name="Invalid")
    request_body = CharacterRequestApiModel(
        characters=[alpha_character, invalid_character],
        deleted_ids=[1000],
    )

    run_async(
        characters_business.handle_incoming_characters(
            request_body,
            CharacterRequestType.set,
        )
    )

    payload_by_server = {server_name: payload for payload, server_name in set_calls}

    assert alpha_character.last_update == now
    assert set(payload_by_server.keys()) == {"alpha", "beta"}
    assert set(payload_by_server["alpha"].keys()) == {1}
    assert payload_by_server["alpha"][1]["last_update"] == now
    assert payload_by_server["beta"] == {}
    assert update_calls == []
    assert delete_calls == []
    assert len(aggregate_calls) == 2
    assert aggregate_calls[0]["deleted_ids"] == set()
    assert aggregate_calls[1]["deleted_ids"] == set()
    assert persisted_deleted_calls == [[]]
    assert persisted_activity_calls == [[]]


def test_handle_incoming_characters_update_persists_deleted_and_activity(
    monkeypatch, run_async
):
    now = "2026-03-15T13:00:00Z"
    update_calls = []
    delete_calls = []
    set_calls = []
    persisted_deleted_calls = []
    persisted_activity_calls = []

    monkeypatch.setattr(characters_business, "SERVER_NAMES_LOWERCASE", ["alpha"])
    monkeypatch.setattr(characters_business, "get_current_datetime_string", lambda: now)

    previous_characters = {
        1: {"id": 1, "name": "Deleted Character", "server_name": "alpha"},
        2: {"id": 2, "name": "Current Character", "server_name": "alpha"},
    }
    monkeypatch.setattr(
        characters_business.redis_client,
        "get_characters_by_server_name_as_dict",
        lambda _server_name: previous_characters,
    )

    expected_activity = [
        {"character_id": 1, "activity_type": "status", "data": {"value": False}}
    ]

    def _aggregate(
        previous_characters,
        incoming_characters,
        previous_character_ids,
        incoming_character_ids,
        deleted_character_ids,
    ):
        assert previous_character_ids == {1, 2}
        assert incoming_character_ids == {2}
        assert deleted_character_ids == {1}
        return expected_activity

    monkeypatch.setattr(
        characters_business,
        "aggregate_character_activity_for_server",
        _aggregate,
    )
    monkeypatch.setattr(
        characters_business.redis_client,
        "set_characters_by_server_name",
        lambda payload, server_name: set_calls.append((payload, server_name)),
    )
    monkeypatch.setattr(
        characters_business.redis_client,
        "update_characters_by_server_name",
        lambda payload, server_name: update_calls.append((payload, server_name)),
    )
    monkeypatch.setattr(
        characters_business.redis_client,
        "delete_characters_by_id_and_server_name",
        lambda ids, server_name: delete_calls.append((ids, server_name)),
    )
    monkeypatch.setattr(
        characters_business,
        "persist_deleted_characters_to_db",
        _amock(lambda characters: persisted_deleted_calls.append(characters)),
    )
    monkeypatch.setattr(
        characters_business,
        "persist_character_activity_to_db",
        _amock(
            lambda activity_events: persisted_activity_calls.append(activity_events)
        ),
    )

    request_body = CharacterRequestApiModel(
        characters=[
            _character(
                2,
                server_name="Alpha",
                location_id=999,
                guild_name="Guild",
                total_level=10,
                group_id=5,
            )
        ],
        deleted_ids=[1, 999],
    )

    run_async(
        characters_business.handle_incoming_characters(
            request_body,
            CharacterRequestType.update,
        )
    )

    assert len(update_calls) == 1
    update_payload, update_server_name = update_calls[0]
    assert update_server_name == "alpha"
    assert set(update_payload.keys()) == {2}
    assert update_payload[2]["last_update"] == now

    assert delete_calls == [({1}, "alpha")]
    assert set_calls == []
    assert persisted_deleted_calls == [[previous_characters[1]]]
    assert persisted_activity_calls == [expected_activity]


def test_handle_incoming_characters_update_combines_multiple_server_changes(
    monkeypatch, run_async
):
    update_calls = []
    delete_calls = []
    persisted_deleted_calls = []
    persisted_activity_calls = []

    monkeypatch.setattr(
        characters_business, "SERVER_NAMES_LOWERCASE", ["alpha", "beta"]
    )
    monkeypatch.setattr(
        characters_business,
        "get_current_datetime_string",
        lambda: "2026-03-15T14:00:00Z",
    )

    previous_by_server = {
        "alpha": {
            1: {"id": 1, "name": "alpha-deleted", "server_name": "alpha"},
            2: {"id": 2, "name": "alpha-current", "server_name": "alpha"},
        },
        "beta": {
            3: {"id": 3, "name": "beta-deleted", "server_name": "beta"},
            4: {"id": 4, "name": "beta-current", "server_name": "beta"},
        },
    }

    monkeypatch.setattr(
        characters_business.redis_client,
        "get_characters_by_server_name_as_dict",
        lambda server_name: previous_by_server[server_name],
    )

    def _aggregate(
        previous_characters,
        incoming_characters,
        previous_character_ids,
        incoming_character_ids,
        deleted_character_ids,
    ):
        if 2 in incoming_character_ids:
            return [
                {"character_id": 2, "activity_type": "status", "data": {"value": True}}
            ]
        if 4 in incoming_character_ids:
            return [
                {"character_id": 4, "activity_type": "status", "data": {"value": True}}
            ]
        return []

    monkeypatch.setattr(
        characters_business,
        "aggregate_character_activity_for_server",
        _aggregate,
    )
    monkeypatch.setattr(
        characters_business.redis_client,
        "update_characters_by_server_name",
        lambda payload, server_name: update_calls.append((payload, server_name)),
    )
    monkeypatch.setattr(
        characters_business.redis_client,
        "delete_characters_by_id_and_server_name",
        lambda ids, server_name: delete_calls.append((ids, server_name)),
    )
    monkeypatch.setattr(
        characters_business,
        "persist_deleted_characters_to_db",
        _amock(lambda characters: persisted_deleted_calls.append(characters)),
    )
    monkeypatch.setattr(
        characters_business,
        "persist_character_activity_to_db",
        _amock(
            lambda activity_events: persisted_activity_calls.append(activity_events)
        ),
    )

    request_body = CharacterRequestApiModel(
        characters=[
            _character(2, server_name="alpha", name="A-2"),
            _character(4, server_name="beta", name="B-4"),
        ],
        deleted_ids=[1, 3],
    )

    run_async(
        characters_business.handle_incoming_characters(
            request_body,
            CharacterRequestType.update,
        )
    )

    assert len(update_calls) == 2
    assert ({1}, "alpha") in delete_calls
    assert ({3}, "beta") in delete_calls
    assert len(persisted_deleted_calls) == 1
    assert {character["id"] for character in persisted_deleted_calls[0]} == {1, 3}
    assert len(persisted_activity_calls) == 1
    assert {event["character_id"] for event in persisted_activity_calls[0]} == {2, 4}
