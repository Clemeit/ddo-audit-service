from contextlib import contextmanager
from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from models.redis import RedisKeys, ServerInfo, ServerSpecificInfo
from models.service import News, PageMessage
import services.redis as redis_service


def _patch_sync_client(monkeypatch, client):
    @contextmanager
    def _client_ctx():
        yield client

    monkeypatch.setattr(redis_service, "get_redis_client", _client_ctx)


def _patch_sync_client_error(monkeypatch, exc):
    @contextmanager
    def _client_ctx():
        raise exc
        yield

    monkeypatch.setattr(redis_service, "get_redis_client", _client_ctx)


def _patch_pipeline_context(monkeypatch, pipeline):
    @contextmanager
    def _pipeline_ctx():
        yield pipeline

    monkeypatch.setattr(redis_service, "get_redis_pipeline", _pipeline_ctx)


def _patch_async_client(monkeypatch, client):
    async def _get_async_client():
        return client

    monkeypatch.setattr(redis_service, "get_async_redis_client", _get_async_client)


def _character_payload(character_id, name, *, group_id=None):
    return {
        "id": character_id,
        "name": name,
        "server_name": "argonnessen",
        "group_id": group_id,
    }


def _lfm_payload(lfm_id):
    return {
        "id": lfm_id,
        "server_name": "argonnessen",
    }


def test_get_characters_by_server_name_as_dict_converts_keys_to_int(monkeypatch):
    client = MagicMock()
    client.json.return_value.get.return_value = {
        "1": _character_payload(1, "Alice"),
        "2": _character_payload(2, "Bob"),
    }
    _patch_sync_client(monkeypatch, client)

    result = redis_service.get_characters_by_server_name_as_dict("Argonnessen")

    assert result == {
        1: _character_payload(1, "Alice"),
        2: _character_payload(2, "Bob"),
    }
    client.json.return_value.get.assert_called_once_with("argonnessen:characters")


def test_get_characters_by_server_name_as_dict_returns_empty_for_none(monkeypatch):
    client = MagicMock()
    client.json.return_value.get.return_value = None
    _patch_sync_client(monkeypatch, client)

    assert redis_service.get_characters_by_server_name_as_dict("argonnessen") == {}


def test_get_all_character_counts_uses_pipeline_and_none_falls_back_to_zero(
    monkeypatch,
):
    monkeypatch.setattr(
        redis_service, "SERVER_NAMES_LOWERCASE", ["argonnessen", "orien"]
    )
    pipeline = MagicMock()
    pipeline.execute.return_value = [4, None]
    _patch_pipeline_context(monkeypatch, pipeline)

    result = redis_service.get_all_character_counts()

    assert result == {
        "argonnessen": 4,
        "orien": 0,
    }
    assert pipeline.json.return_value.objlen.call_count == 2


def test_set_characters_by_server_name_sets_json_root(monkeypatch):
    client = MagicMock()
    _patch_sync_client(monkeypatch, client)
    payload = {1: _character_payload(1, "Alice")}

    redis_service.set_characters_by_server_name(payload, "Argonnessen")

    client.json.return_value.set.assert_called_once_with(
        name="argonnessen:characters",
        path="$",
        obj=payload,
    )


def test_delete_characters_by_id_and_server_name_early_return_for_empty(monkeypatch):
    client = MagicMock()
    _patch_sync_client(monkeypatch, client)

    redis_service.delete_characters_by_id_and_server_name([], "Argonnessen")

    client.pipeline.assert_not_called()


def test_delete_characters_by_id_and_server_name_deletes_each_id(monkeypatch):
    client = MagicMock()
    pipeline = MagicMock()
    client.pipeline.return_value.__enter__.return_value = pipeline
    _patch_sync_client(monkeypatch, client)

    redis_service.delete_characters_by_id_and_server_name([100, 200], "Argonnessen")

    assert pipeline.json.return_value.delete.call_args_list[0].kwargs == {
        "key": "argonnessen:characters",
        "path": 100,
    }
    assert pipeline.json.return_value.delete.call_args_list[1].kwargs == {
        "key": "argonnessen:characters",
        "path": 200,
    }
    pipeline.execute.assert_called_once()


def test_get_character_by_name_and_server_name_returns_character_model(monkeypatch):
    monkeypatch.setattr(
        redis_service,
        "get_characters_by_server_name_as_dict",
        lambda server_name: {
            1: _character_payload(1, "Alice"),
            2: _character_payload(2, "Bob"),
        },
    )

    result = redis_service.get_character_by_name_and_server_name("ALICE", "argonnessen")

    assert result is not None
    assert result.id == 1
    assert result.name == "Alice"


def test_get_character_by_name_and_server_name_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(
        redis_service,
        "get_characters_by_server_name_as_dict",
        lambda server_name: {1: _character_payload(1, "Alice")},
    )

    assert (
        redis_service.get_character_by_name_and_server_name("Charlie", "argonnessen")
        is None
    )


def test_get_character_by_id_returns_character_model(monkeypatch):
    monkeypatch.setattr(redis_service, "SERVER_NAMES_LOWERCASE", ["alpha", "beta"])
    monkeypatch.setattr(
        redis_service,
        "get_character_ids_by_server_name",
        lambda server_name: [7] if server_name == "beta" else [],
    )

    client = MagicMock()
    client.json.return_value.get.return_value = _character_payload(7, "Rogue")
    _patch_sync_client(monkeypatch, client)

    result = redis_service.get_character_by_id(7)

    assert result is not None
    assert result.id == 7
    assert result.name == "Rogue"
    client.json.return_value.get.assert_called_once_with("beta:characters", 7)


def test_get_character_by_id_returns_none_when_redis_get_raises(monkeypatch):
    monkeypatch.setattr(redis_service, "SERVER_NAMES_LOWERCASE", ["argonnessen"])
    monkeypatch.setattr(
        redis_service,
        "get_character_ids_by_server_name",
        lambda server_name: [9],
    )

    client = MagicMock()
    client.json.return_value.get.side_effect = RuntimeError("redis down")
    _patch_sync_client(monkeypatch, client)

    assert redis_service.get_character_by_id(9) is None


def test_get_characters_by_group_id_filters_across_servers(monkeypatch):
    monkeypatch.setattr(redis_service, "SERVER_NAMES_LOWERCASE", ["alpha", "beta"])

    def _get_server_characters(server_name):
        if server_name == "alpha":
            return {
                1: _character_payload(1, "One", group_id=42),
                2: _character_payload(2, "Two", group_id=10),
            }
        return {
            3: _character_payload(3, "Three", group_id=42),
        }

    monkeypatch.setattr(
        redis_service,
        "get_characters_by_server_name_as_dict",
        _get_server_characters,
    )

    result = redis_service.get_characters_by_group_id(42)

    assert sorted(result.keys()) == [1, 3]
    assert result[1].group_id == 42
    assert result[3].group_id == 42


def test_get_characters_by_group_id_returns_empty_for_non_positive_group():
    assert redis_service.get_characters_by_group_id(0) == {}


def test_get_lfms_by_server_name_as_dict_converts_keys_to_int(monkeypatch):
    client = MagicMock()
    client.json.return_value.get.return_value = {
        "1": _lfm_payload(1),
        "2": _lfm_payload(2),
    }
    _patch_sync_client(monkeypatch, client)

    result = redis_service.get_lfms_by_server_name_as_dict("Argonnessen")

    assert result == {
        1: _lfm_payload(1),
        2: _lfm_payload(2),
    }
    client.json.return_value.get.assert_called_once_with("argonnessen:lfms")


def test_get_lfms_by_server_name_as_dict_returns_empty_for_none(monkeypatch):
    client = MagicMock()
    client.json.return_value.get.return_value = None
    _patch_sync_client(monkeypatch, client)

    assert redis_service.get_lfms_by_server_name_as_dict("argonnessen") == {}


def test_get_all_lfm_counts_uses_pipeline_and_none_falls_back_to_zero(monkeypatch):
    monkeypatch.setattr(
        redis_service, "SERVER_NAMES_LOWERCASE", ["argonnessen", "orien"]
    )
    pipeline = MagicMock()
    pipeline.execute.return_value = [3, None]
    _patch_pipeline_context(monkeypatch, pipeline)

    result = redis_service.get_all_lfm_counts()

    assert result == {
        "argonnessen": 3,
        "orien": 0,
    }
    assert pipeline.json.return_value.objlen.call_count == 2


def test_set_lfms_by_server_name_sets_json_root(monkeypatch):
    client = MagicMock()
    _patch_sync_client(monkeypatch, client)
    payload = {1: _lfm_payload(1)}

    redis_service.set_lfms_by_server_name(payload, "Argonnessen")

    client.json.return_value.set.assert_called_once_with(
        "argonnessen:lfms",
        path="$",
        obj=payload,
    )


def test_delete_lfms_by_id_and_server_name_early_return_for_empty(monkeypatch):
    client = MagicMock()
    _patch_sync_client(monkeypatch, client)

    redis_service.delete_lfms_by_id_and_server_name([], "Argonnessen")

    client.pipeline.assert_not_called()


def test_delete_lfms_by_id_and_server_name_deletes_each_id(monkeypatch):
    client = MagicMock()
    pipeline = MagicMock()
    client.pipeline.return_value.__enter__.return_value = pipeline
    _patch_sync_client(monkeypatch, client)

    redis_service.delete_lfms_by_id_and_server_name([11, 22], "Argonnessen")

    assert pipeline.json.return_value.delete.call_args_list[0].kwargs == {
        "key": "argonnessen:lfms",
        "path": 11,
    }
    assert pipeline.json.return_value.delete.call_args_list[1].kwargs == {
        "key": "argonnessen:lfms",
        "path": 22,
    }
    pipeline.execute.assert_called_once()


def test_get_server_info_as_dict_reads_servers_path(monkeypatch):
    client = MagicMock()
    client.json.return_value.get.return_value = {"argonnessen": {"is_online": True}}
    _patch_sync_client(monkeypatch, client)

    result = redis_service.get_server_info_as_dict()

    assert result == {"argonnessen": {"is_online": True}}
    client.json.return_value.get.assert_called_once_with(
        RedisKeys.SERVER_INFO.value,
        "servers",
    )


def test_merge_server_info_merges_model_dump(monkeypatch):
    client = MagicMock()
    _patch_sync_client(monkeypatch, client)
    server_info = ServerInfo(
        servers={
            "argonnessen": ServerSpecificInfo(is_online=True),
        }
    )

    redis_service.merge_server_info(server_info)

    client.json.return_value.merge.assert_called_once_with(
        RedisKeys.SERVER_INFO.value,
        path="$",
        obj={"servers": {"argonnessen": {"is_online": True}}},
    )


def test_get_news_as_dict_reads_news_key(monkeypatch):
    client = MagicMock()
    client.json.return_value.get.return_value = [{"id": 1, "message": "Hello"}]
    _patch_sync_client(monkeypatch, client)

    result = redis_service.get_news_as_dict()

    assert result == [{"id": 1, "message": "Hello"}]
    client.json.return_value.get.assert_called_once_with(RedisKeys.NEWS.value)


def test_set_news_serializes_models(monkeypatch):
    client = MagicMock()
    _patch_sync_client(monkeypatch, client)
    news_items = [News(id=1, message="Patch notes", date="2026-03-15")]

    redis_service.set_news(news_items)

    client.json.return_value.set.assert_called_once_with(
        RedisKeys.NEWS.value,
        path="$",
        obj=[{"id": 1, "date": "2026-03-15", "message": "Patch notes"}],
    )


def test_get_page_messages_as_dict_reads_page_messages_key(monkeypatch):
    client = MagicMock()
    client.json.return_value.get.return_value = [{"id": 2, "message": "Downtime"}]
    _patch_sync_client(monkeypatch, client)

    result = redis_service.get_page_messages_as_dict()

    assert result == [{"id": 2, "message": "Downtime"}]
    client.json.return_value.get.assert_called_once_with(RedisKeys.PAGE_MESSAGES.value)


def test_set_page_messages_serializes_models(monkeypatch):
    client = MagicMock()
    _patch_sync_client(monkeypatch, client)
    messages = [
        PageMessage(
            id=10,
            message="Maintenance",
            affected_pages=["/"],
            dismissable=True,
            type="warning",
        )
    ]

    redis_service.set_page_messages(messages)

    client.json.return_value.set.assert_called_once_with(
        RedisKeys.PAGE_MESSAGES.value,
        path="$",
        obj=[
            {
                "id": 10,
                "message": "Maintenance",
                "affected_pages": ["/"],
                "dismissable": True,
                "type": "warning",
                "start_date": None,
                "end_date": None,
            }
        ],
    )


def test_traffic_increment_noop_when_disabled(monkeypatch, run_async):
    monkeypatch.setattr(redis_service, "TRAFFIC_COUNTERS_ENABLED", False)

    async def _unexpected_call():
        raise AssertionError("get_async_redis_client should not be called")

    monkeypatch.setattr(redis_service, "get_async_redis_client", _unexpected_call)

    run_async(
        redis_service.traffic_increment(
            ip="1.2.3.4",
            route="/health",
            method="GET",
            status=200,
            bytes_out=10,
        )
    )


def test_traffic_increment_queues_expected_pipeline_operations(monkeypatch, run_async):
    monkeypatch.setattr(redis_service, "TRAFFIC_COUNTERS_ENABLED", True)
    monkeypatch.setattr(redis_service, "TRAFFIC_COUNTERS_TTL_HOURS", 1)
    monkeypatch.setattr(redis_service, "TRAFFIC_COUNTERS_PREFIX", "traffic")
    monkeypatch.setattr(redis_service, "_traffic_bucket_id", lambda now_s=None: 10)

    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=[])
    client = MagicMock()
    client.pipeline.return_value = pipe
    _patch_async_client(monkeypatch, client)

    run_async(
        redis_service.traffic_increment(
            ip="9.9.9.9",
            route="/api/quests",
            method="GET",
            status=200,
            bytes_out=512,
        )
    )

    pipe.zincrby.assert_any_call("traffic:req:ip:10", 1, "9.9.9.9")
    pipe.zincrby.assert_any_call("traffic:req:route:10", 1, "/api/quests")
    pipe.zincrby.assert_any_call("traffic:bytes_out:ip:10", 512, "9.9.9.9")
    pipe.zincrby.assert_any_call("traffic:bytes_out:route:10", 512, "/api/quests")
    pipe.incr.assert_called_once_with("traffic:req:total:10")
    pipe.incrby.assert_called_once_with("traffic:bytes_out:total:10", 512)
    pipe.hincrby.assert_any_call("traffic:req:method:10", "GET", 1)
    pipe.hincrby.assert_any_call("traffic:req:status:10", "200", 1)
    assert pipe.expire.call_count == 8
    pipe.execute.assert_awaited_once()


def test_traffic_increment_swallows_async_redis_errors(monkeypatch, run_async):
    monkeypatch.setattr(redis_service, "TRAFFIC_COUNTERS_ENABLED", True)

    async def _raise_error():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(redis_service, "get_async_redis_client", _raise_error)

    run_async(
        redis_service.traffic_increment(
            ip=None,
            route=None,
            method=None,
            status=500,
            bytes_out=None,
        )
    )


def test_traffic_top_ips_uses_requests_metric_by_default(monkeypatch, run_async):
    top_mock = AsyncMock(return_value=[("1.1.1.1", 3.0)])
    monkeypatch.setattr(redis_service, "_traffic_top_zset", top_mock)

    result = run_async(redis_service.traffic_top_ips(minutes=5, limit=1))

    assert result == [{"ip": "1.1.1.1", "requests": 3.0}]
    top_mock.assert_awaited_once_with(suffix="req:ip", minutes=5, limit=1)


def test_traffic_top_ips_uses_bytes_out_metric(monkeypatch, run_async):
    top_mock = AsyncMock(return_value=[("2.2.2.2", 1024.0)])
    monkeypatch.setattr(redis_service, "_traffic_top_zset", top_mock)

    result = run_async(
        redis_service.traffic_top_ips(
            minutes=15,
            metric="bytes_out",
            limit=2,
        )
    )

    assert result == [{"ip": "2.2.2.2", "bytes_out": 1024.0}]
    top_mock.assert_awaited_once_with(suffix="bytes_out:ip", minutes=15, limit=2)


def test_traffic_top_routes_maps_results(monkeypatch, run_async):
    top_mock = AsyncMock(return_value=[("/quests", 7.0)])
    monkeypatch.setattr(redis_service, "_traffic_top_zset", top_mock)

    result = run_async(redis_service.traffic_top_routes(minutes=60, limit=5))

    assert result == [{"route": "/quests", "requests": 7.0}]
    top_mock.assert_awaited_once_with(suffix="req:route", minutes=60, limit=5)


def test_get_cached_user_auth_version_decodes_bytes_and_handles_missing(monkeypatch):
    client = MagicMock()
    client.get.side_effect = [b"5", None]
    _patch_sync_client(monkeypatch, client)

    assert redis_service.get_cached_user_auth_version(10) == 5
    assert redis_service.get_cached_user_auth_version(10) is None


def test_get_cached_user_auth_version_returns_none_for_invalid_value(monkeypatch):
    client = MagicMock()
    client.get.return_value = b"not-an-int"
    _patch_sync_client(monkeypatch, client)

    assert redis_service.get_cached_user_auth_version(5) is None


def test_cache_user_auth_version_sets_ttl(monkeypatch):
    client = MagicMock()
    _patch_sync_client(monkeypatch, client)
    monkeypatch.setattr(redis_service, "AUTH_CACHE_TTL_SECONDS", 321)

    redis_service.cache_user_auth_version(7, 9)

    client.setex.assert_called_once_with("auth:user:version:7", 321, 9)


def test_get_cached_auth_session_decodes_dict_payload(monkeypatch):
    client = MagicMock()
    client.get.return_value = b'{"session_id":"s1","user_id":1}'
    _patch_sync_client(monkeypatch, client)

    result = redis_service.get_cached_auth_session("s1")

    assert result == {"session_id": "s1", "user_id": 1}


def test_get_cached_auth_session_rejects_invalid_payloads(monkeypatch):
    client = MagicMock()
    client.get.side_effect = [b"[1,2,3]", b"not-json"]
    _patch_sync_client(monkeypatch, client)

    assert redis_service.get_cached_auth_session("s1") is None
    assert redis_service.get_cached_auth_session("s2") is None


def test_cache_auth_session_filters_keys_and_uses_computed_ttl(monkeypatch):
    client = MagicMock()
    _patch_sync_client(monkeypatch, client)
    monkeypatch.setattr(redis_service, "_compute_auth_cache_ttl", lambda expires: 42)

    now = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
    redis_service.cache_auth_session(
        "sess-1",
        {
            "session_id": "sess-1",
            "user_id": 1,
            "auth_version": 4,
            "expires_at": now,
            "revoked_at": None,
            "updated_at": now,
            "internal_only_field": "ignore-me",
        },
    )

    client.setex.assert_called_once()
    key, ttl, payload_raw = client.setex.call_args.args
    payload = json.loads(payload_raw)

    assert key == "auth:session:sess-1"
    assert ttl == 42
    assert payload == {
        "session_id": "sess-1",
        "user_id": 1,
        "auth_version": 4,
        "expires_at": "2026-03-15T10:00:00+00:00",
        "revoked_at": None,
        "updated_at": "2026-03-15T10:00:00+00:00",
    }


def test_clear_cached_auth_session_deletes_cache_key(monkeypatch):
    client = MagicMock()
    _patch_sync_client(monkeypatch, client)

    redis_service.clear_cached_auth_session("session-abc")

    client.delete.assert_called_once_with("auth:session:session-abc")


def test_get_active_quest_session_state_parses_json(monkeypatch):
    client = MagicMock()
    client.get.return_value = (
        '{"quest_id": 5, "entry_timestamp": "2026-03-15T11:00:00+00:00"}'
    )
    _patch_sync_client(monkeypatch, client)

    result = redis_service.get_active_quest_session_state(10)

    assert result == {
        "quest_id": 5,
        "entry_timestamp": "2026-03-15T11:00:00+00:00",
    }


def test_get_active_quest_session_state_returns_none_when_missing(monkeypatch):
    client = MagicMock()
    client.get.return_value = None
    _patch_sync_client(monkeypatch, client)

    assert redis_service.get_active_quest_session_state(10) is None


def test_get_active_quest_session_state_returns_none_on_redis_error(monkeypatch):
    _patch_sync_client_error(monkeypatch, RuntimeError("redis down"))

    assert redis_service.get_active_quest_session_state(99) is None


def test_set_active_quest_session_state_stores_with_48h_ttl(monkeypatch):
    client = MagicMock()
    _patch_sync_client(monkeypatch, client)
    ts = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

    redis_service.set_active_quest_session_state(77, 88, ts)

    key, ttl, payload_raw = client.setex.call_args.args
    assert key == "active_quest_session:77"
    assert ttl == 172800
    assert json.loads(payload_raw) == {
        "quest_id": 88,
        "entry_timestamp": "2026-03-15T12:00:00+00:00",
    }


def test_batch_get_active_quest_session_states_parses_valid_and_invalid_json(
    monkeypatch,
):
    client = MagicMock()
    pipeline = MagicMock()
    pipeline.execute.return_value = [
        b'{"quest_id": 1, "entry_timestamp": "2026-03-15T01:00:00+00:00"}',
        b"not-json",
        None,
    ]
    client.pipeline.return_value = pipeline
    _patch_sync_client(monkeypatch, client)

    result = redis_service.batch_get_active_quest_session_states([1, 2, 3])

    assert result == {
        1: {"quest_id": 1, "entry_timestamp": "2026-03-15T01:00:00+00:00"},
        2: None,
        3: None,
    }
    assert pipeline.get.call_count == 3


def test_batch_get_active_quest_session_states_returns_none_map_on_error(monkeypatch):
    _patch_sync_client_error(monkeypatch, RuntimeError("redis down"))

    result = redis_service.batch_get_active_quest_session_states([10, 11])

    assert result == {10: None, 11: None}


def test_batch_update_active_quest_session_states_early_return_for_no_changes(
    monkeypatch,
):
    client = MagicMock()
    _patch_sync_client(monkeypatch, client)

    redis_service.batch_update_active_quest_session_states({}, [])

    client.pipeline.assert_not_called()


def test_batch_update_active_quest_session_states_sets_and_deletes(monkeypatch):
    client = MagicMock()
    pipeline = MagicMock()
    client.pipeline.return_value = pipeline
    _patch_sync_client(monkeypatch, client)

    redis_service.batch_update_active_quest_session_states(
        updates_set={
            1: {"quest_id": 100, "entry_timestamp": "2026-03-15T10:00:00+00:00"}
        },
        updates_clear=[2],
    )

    pipeline.setex.assert_called_once_with(
        "active_quest_session:1",
        172800,
        '{"quest_id": 100, "entry_timestamp": "2026-03-15T10:00:00+00:00"}',
    )
    pipeline.delete.assert_called_once_with("active_quest_session:2")
    pipeline.execute.assert_called_once()


def test_store_one_time_user_settings_sets_payload_and_ttl(monkeypatch):
    client = MagicMock()
    _patch_sync_client(monkeypatch, client)

    redis_service.store_one_time_user_settings("user-1", {"theme": "dark"})

    client.json.return_value.set.assert_called_once_with(
        "one_time_user_settings:user-1",
        path="$",
        obj={"theme": "dark"},
    )
    client.expire.assert_called_once_with("one_time_user_settings:user-1", 300)


def test_get_one_time_user_settings_uses_atomic_getdel_and_parses_json(monkeypatch):
    client = MagicMock()
    client.eval.return_value = b'{"lang":"en"}'
    _patch_sync_client(monkeypatch, client)

    result = redis_service.get_one_time_user_settings("user-99")

    assert result == {"lang": "en"}
    client.eval.assert_called_once_with(
        redis_service._ONE_TIME_USER_SETTINGS_GETDEL_LUA,
        1,
        "one_time_user_settings:user-99",
    )


def test_get_one_time_user_settings_returns_none_for_missing_or_invalid_json(
    monkeypatch,
):
    client = MagicMock()
    client.eval.side_effect = [None, b"not-json"]
    _patch_sync_client(monkeypatch, client)

    assert redis_service.get_one_time_user_settings("user-1") is None
    assert redis_service.get_one_time_user_settings("user-2") is None


def test_one_time_user_settings_exists_checks_exists_equals_one(monkeypatch):
    client = MagicMock()
    client.exists.side_effect = [1, 0]
    _patch_sync_client(monkeypatch, client)

    assert redis_service.one_time_user_settings_exists("user-1") is True
    assert redis_service.one_time_user_settings_exists("user-2") is False


def test_bulk_update_characters_builds_json_merge_operations(monkeypatch):
    captured = {}

    def _capture(operations):
        captured["operations"] = operations

    monkeypatch.setattr(redis_service, "execute_batch_operations", _capture)

    redis_service.bulk_update_characters(
        {
            "Argonnessen": {1: _character_payload(1, "Alice")},
            "Orien": {},
            "Thelanis": {2: _character_payload(2, "Bob")},
        }
    )

    assert captured["operations"] == [
        (
            "json_merge",
            {
                "name": "argonnessen:characters",
                "path": "$",
                "obj": {1: _character_payload(1, "Alice")},
            },
        ),
        (
            "json_merge",
            {
                "name": "thelanis:characters",
                "path": "$",
                "obj": {2: _character_payload(2, "Bob")},
            },
        ),
    ]


def test_bulk_update_characters_skips_execute_for_empty_updates(monkeypatch):
    called = {"value": False}

    def _mark_called(_operations):
        called["value"] = True

    monkeypatch.setattr(redis_service, "execute_batch_operations", _mark_called)

    redis_service.bulk_update_characters({"Argonnessen": {}})

    assert called["value"] is False


def test_bulk_update_lfms_builds_json_merge_operations(monkeypatch):
    captured = {}

    def _capture(operations):
        captured["operations"] = operations

    monkeypatch.setattr(redis_service, "execute_batch_operations", _capture)

    redis_service.bulk_update_lfms(
        {
            "Argonnessen": {1: _lfm_payload(1)},
            "Orien": {},
            "Thelanis": {2: _lfm_payload(2)},
        }
    )

    assert captured["operations"] == [
        (
            "json_merge",
            {
                "name": "argonnessen:lfms",
                "path": "$",
                "obj": {1: _lfm_payload(1)},
            },
        ),
        (
            "json_merge",
            {
                "name": "thelanis:lfms",
                "path": "$",
                "obj": {2: _lfm_payload(2)},
            },
        ),
    ]


def test_bulk_update_lfms_skips_execute_for_empty_updates(monkeypatch):
    called = {"value": False}

    def _mark_called(_operations):
        called["value"] = True

    monkeypatch.setattr(redis_service, "execute_batch_operations", _mark_called)

    redis_service.bulk_update_lfms({"Argonnessen": {}})

    assert called["value"] is False
