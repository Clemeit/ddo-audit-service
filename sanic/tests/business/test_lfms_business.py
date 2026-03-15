import business.lfms as lfms_business
from models.api import LfmRequestApiModel, LfmRequestType
from models.character import Character
from models.lfm import Lfm, LfmActivity, LfmActivityEvent, LfmActivityType


_MISSING = object()


def _member(member_id: int, name: str) -> Character:
    return Character(id=member_id, name=name, server_name="alpha")


def _lfm(
    lfm_id: int,
    *,
    server_name: str = "alpha",
    quest_id: int | None = None,
    comment: str | None = None,
    members=_MISSING,
    activity=_MISSING,
    last_update: str = "2026-03-15T00:00:00Z",
) -> Lfm:
    members_value = [] if members is _MISSING else members
    activity_value = [] if activity is _MISSING else activity
    return Lfm(
        id=lfm_id,
        server_name=server_name,
        quest_id=quest_id,
        comment=comment,
        members=members_value,
        activity=activity_value,
        last_update=last_update,
    )


def _event_tag_value(activity_event: dict) -> str:
    tag = activity_event["tag"]
    return tag.value if hasattr(tag, "value") else tag


def test_hydrate_lfms_with_activity_merges_activity_into_payloads():
    lfms = {
        1: {"id": 1, "comment": "first"},
        2: {"id": 2, "comment": "second"},
    }
    lfm_activity = {
        1: [{"timestamp": "ts-1", "events": [{"tag": "posted"}]}],
        2: [{"timestamp": "ts-2", "events": [{"tag": "comment"}]}],
    }

    hydrated = lfms_business.hydrate_lfms_with_activity(lfms, lfm_activity)

    assert hydrated[1]["activity"] == lfm_activity[1]
    assert hydrated[2]["activity"] == lfm_activity[2]
    assert hydrated[1]["comment"] == "first"
    assert hydrated[2]["comment"] == "second"


def test_get_lfm_activity_marks_new_lfms_as_posted():
    current_lfms = {
        7: _lfm(
            7,
            quest_id=100,
            comment="new lfm",
            members=[_member(1, "One")],
            last_update="2026-03-15T10:00:00Z",
        )
    }

    activity = lfms_business.get_lfm_activity(
        previous_lfms={}, current_lfms=current_lfms
    )

    assert set(activity.keys()) == {7}
    assert len(activity[7]) == 1
    assert activity[7][0]["timestamp"] == "2026-03-15T10:00:00Z"
    assert [_event_tag_value(event) for event in activity[7][0]["events"]] == [
        LfmActivityType.posted.value
    ]


def test_get_lfm_activity_tracks_quest_comment_and_member_changes_with_history():
    previous_activity = [
        LfmActivity(
            timestamp="2026-03-14T10:00:00Z",
            events=[LfmActivityEvent(tag=LfmActivityType.posted.value)],
        )
    ]
    previous_lfms = {
        1: _lfm(
            1,
            quest_id=100,
            comment="old comment",
            members=[_member(1, "One"), _member(2, "Second")],
            activity=previous_activity,
            last_update="2026-03-14T10:00:00Z",
        )
    }
    current_lfms = {
        1: _lfm(
            1,
            quest_id=200,
            comment="updated comment",
            members=[_member(1, "One"), _member(3, "Third")],
            last_update="2026-03-15T10:00:00Z",
        )
    }

    activity = lfms_business.get_lfm_activity(previous_lfms, current_lfms)

    assert len(activity[1]) == 2
    assert activity[1][0]["timestamp"] == "2026-03-14T10:00:00Z"

    latest_events = activity[1][1]["events"]
    assert [_event_tag_value(event) for event in latest_events] == [
        LfmActivityType.quest.value,
        LfmActivityType.comment.value,
        LfmActivityType.member_left.value,
        LfmActivityType.member_joined.value,
    ]
    assert latest_events[0]["data"] == "200"
    assert latest_events[1]["data"] == "updated comment"
    assert latest_events[2]["data"] == "Second"
    assert latest_events[3]["data"] == "Third"


def test_get_lfm_activity_uses_zero_when_quest_changes_to_none():
    previous_lfms = {
        1: _lfm(
            1,
            quest_id=100,
            comment="unchanged",
            members=[_member(1, "One")],
            last_update="2026-03-14T10:00:00Z",
        )
    }
    current_lfms = {
        1: _lfm(
            1,
            quest_id=None,
            comment="unchanged",
            members=[_member(1, "One")],
            last_update="2026-03-15T10:00:00Z",
        )
    }

    activity = lfms_business.get_lfm_activity(previous_lfms, current_lfms)

    quest_events = [
        event
        for event in activity[1][-1]["events"]
        if _event_tag_value(event) == LfmActivityType.quest.value
    ]
    assert len(quest_events) == 1
    assert quest_events[0]["data"] == "0"


def test_get_lfm_activity_skips_entries_that_raise_processing_errors(monkeypatch):
    printed = []

    monkeypatch.setattr("builtins.print", lambda message: printed.append(message))

    previous_lfms = {
        1: _lfm(
            1,
            quest_id=100,
            comment="stable",
            members=[_member(1, "One")],
        )
    }
    # members=None causes a TypeError while computing member id sets.
    current_lfms = {
        1: Lfm(
            id=1,
            server_name="alpha",
            quest_id=100,
            comment="stable",
            members=None,
            activity=[],
            last_update="2026-03-15T11:00:00Z",
        )
    }

    activity = lfms_business.get_lfm_activity(previous_lfms, current_lfms)

    assert activity == {}
    assert any("Error processing LFM ID 1" in line for line in printed)


def test_handle_incoming_lfms_set_filters_invalid_servers_and_sets_cache(monkeypatch):
    now = "2026-03-15T12:00:00Z"
    set_calls = []
    update_calls = []
    delete_calls = []

    monkeypatch.setattr(lfms_business, "SERVER_NAMES_LOWERCASE", ["alpha", "beta"])
    monkeypatch.setattr(lfms_business, "get_current_datetime_string", lambda: now)
    monkeypatch.setattr(
        lfms_business.redis_client,
        "get_lfms_by_server_name",
        lambda _server_name: {},
    )
    monkeypatch.setattr(
        lfms_business,
        "get_lfm_activity",
        lambda _previous, _current: {},
    )

    def _hydrate(incoming_lfms, lfm_activity):
        return {
            lfm_id: {**lfm, "activity": lfm_activity.get(lfm_id, [])}
            for lfm_id, lfm in incoming_lfms.items()
        }

    monkeypatch.setattr(lfms_business, "hydrate_lfms_with_activity", _hydrate)
    monkeypatch.setattr(
        lfms_business.redis_client,
        "set_lfms_by_server_name",
        lambda payload, server_name: set_calls.append((payload, server_name)),
    )
    monkeypatch.setattr(
        lfms_business.redis_client,
        "update_lfms_by_server_name",
        lambda payload, server_name: update_calls.append((payload, server_name)),
    )
    monkeypatch.setattr(
        lfms_business.redis_client,
        "delete_lfms_by_id_and_server_name",
        lambda ids, server_name: delete_calls.append((ids, server_name)),
    )

    request_body = LfmRequestApiModel(
        lfms=[
            _lfm(11, server_name="Alpha", comment="valid"),
            _lfm(999, server_name="Unknown", comment="ignored"),
        ],
        deleted_ids=[404],
    )

    lfms_business.handle_incoming_lfms(request_body, LfmRequestType.set)

    payload_by_server = {server_name: payload for payload, server_name in set_calls}

    assert set(payload_by_server.keys()) == {"alpha", "beta"}
    assert set(payload_by_server["alpha"].keys()) == {11}
    assert payload_by_server["alpha"][11]["last_update"] == now
    assert payload_by_server["beta"] == {}
    assert update_calls == []
    assert delete_calls == []


def test_handle_incoming_lfms_update_calls_update_and_delete(monkeypatch):
    update_calls = []
    delete_calls = []
    set_calls = []

    monkeypatch.setattr(lfms_business, "SERVER_NAMES_LOWERCASE", ["alpha"])
    monkeypatch.setattr(
        lfms_business,
        "get_current_datetime_string",
        lambda: "2026-03-15T13:00:00Z",
    )
    monkeypatch.setattr(
        lfms_business.redis_client,
        "get_lfms_by_server_name",
        lambda _server_name: {5: _lfm(5, quest_id=10, comment="old")},
    )

    expected_activity = {5: [{"timestamp": "2026-03-15T13:00:00Z", "events": []}]}
    expected_hydrated = {
        5: {
            "id": 5,
            "server_name": "alpha",
            "comment": "new",
            "activity": expected_activity[5],
        }
    }

    monkeypatch.setattr(
        lfms_business,
        "get_lfm_activity",
        lambda _previous, _current: expected_activity,
    )
    monkeypatch.setattr(
        lfms_business,
        "hydrate_lfms_with_activity",
        lambda _incoming, _activity: expected_hydrated,
    )
    monkeypatch.setattr(
        lfms_business.redis_client,
        "set_lfms_by_server_name",
        lambda payload, server_name: set_calls.append((payload, server_name)),
    )
    monkeypatch.setattr(
        lfms_business.redis_client,
        "update_lfms_by_server_name",
        lambda payload, server_name: update_calls.append((payload, server_name)),
    )
    monkeypatch.setattr(
        lfms_business.redis_client,
        "delete_lfms_by_id_and_server_name",
        lambda ids, server_name: delete_calls.append((ids, server_name)),
    )

    request_body = LfmRequestApiModel(
        lfms=[_lfm(5, server_name="alpha", quest_id=20, comment="new")],
        deleted_ids=[100, 200],
    )

    lfms_business.handle_incoming_lfms(request_body, LfmRequestType.update)

    assert update_calls == [(expected_hydrated, "alpha")]
    assert delete_calls == [({100, 200}, "alpha")]
    assert set_calls == []
