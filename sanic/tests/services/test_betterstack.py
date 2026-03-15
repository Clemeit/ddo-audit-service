from datetime import datetime
from unittest.mock import MagicMock

import requests

import services.betterstack as betterstack_service


def test_should_send_heartbeat_respects_interval_window():
    service = betterstack_service.BetterStackService()
    heartbeat_type = betterstack_service.HeartbeatType.SERVER_INFO

    now = int(datetime.now().timestamp())
    service.heartbeat_config[heartbeat_type]["interval"] = 30
    service.heartbeat_config[heartbeat_type]["last_heartbeat"] = now - 31
    assert service._should_send_heartbeat(heartbeat_type) is True

    service.heartbeat_config[heartbeat_type]["last_heartbeat"] = now - 10
    assert service._should_send_heartbeat(heartbeat_type) is False


def test_send_heartbeat_request_posts_to_expected_url(monkeypatch):
    service = betterstack_service.BetterStackService()
    response = MagicMock()

    def _fake_post(url, timeout):
        assert url == f"{service.api_url}abc123"
        assert timeout == 10
        return response

    monkeypatch.setattr(betterstack_service.requests, "post", _fake_post)

    assert (
        service._send_heartbeat_request(
            "abc123", betterstack_service.HeartbeatType.CHARACTER_COLLECTIONS
        )
        is True
    )
    response.raise_for_status.assert_called_once()


def test_send_heartbeat_request_returns_false_on_request_error(monkeypatch):
    service = betterstack_service.BetterStackService()

    def _raise_timeout(*args, **kwargs):
        raise requests.exceptions.Timeout("request timed out")

    monkeypatch.setattr(betterstack_service.requests, "post", _raise_timeout)

    assert (
        service._send_heartbeat_request(
            "abc123", betterstack_service.HeartbeatType.LFM_COLLECTIONS
        )
        is False
    )


def test_send_heartbeat_returns_false_when_key_is_missing():
    service = betterstack_service.BetterStackService()
    heartbeat_type = betterstack_service.HeartbeatType.SERVER_INFO
    service.heartbeat_config[heartbeat_type]["key"] = None

    assert service.send_heartbeat(heartbeat_type) is False


def test_send_heartbeat_returns_false_when_interval_not_reached(monkeypatch):
    service = betterstack_service.BetterStackService()
    heartbeat_type = betterstack_service.HeartbeatType.SERVER_INFO
    service.heartbeat_config[heartbeat_type]["key"] = "server-key"

    monkeypatch.setattr(service, "_should_send_heartbeat", lambda _: False)
    send_mock = MagicMock(return_value=True)
    monkeypatch.setattr(service, "_send_heartbeat_request", send_mock)

    assert service.send_heartbeat(heartbeat_type) is False
    send_mock.assert_not_called()


def test_send_heartbeat_success_updates_last_timestamp(monkeypatch):
    service = betterstack_service.BetterStackService()
    heartbeat_type = betterstack_service.HeartbeatType.SERVER_INFO
    service.heartbeat_config[heartbeat_type]["key"] = "server-key"
    service.heartbeat_config[heartbeat_type]["last_heartbeat"] = 0

    monkeypatch.setattr(service, "_should_send_heartbeat", lambda _: True)
    monkeypatch.setattr(service, "_send_heartbeat_request", lambda key, hb_type: True)

    assert service.send_heartbeat(heartbeat_type) is True
    assert service.heartbeat_config[heartbeat_type]["last_heartbeat"] > 0


def test_send_heartbeat_failure_keeps_existing_last_timestamp(monkeypatch):
    service = betterstack_service.BetterStackService()
    heartbeat_type = betterstack_service.HeartbeatType.SERVER_INFO
    service.heartbeat_config[heartbeat_type]["key"] = "server-key"
    service.heartbeat_config[heartbeat_type]["last_heartbeat"] = 777

    monkeypatch.setattr(service, "_should_send_heartbeat", lambda _: True)
    monkeypatch.setattr(service, "_send_heartbeat_request", lambda key, hb_type: False)

    assert service.send_heartbeat(heartbeat_type) is False
    assert service.heartbeat_config[heartbeat_type]["last_heartbeat"] == 777


def test_server_info_heartbeat_uses_global_service(monkeypatch):
    service = MagicMock()
    service.send_heartbeat.return_value = True
    monkeypatch.setattr(betterstack_service, "_betterstack_service", service)

    result = betterstack_service.server_info_heartbeat()

    assert result is True
    service.send_heartbeat.assert_called_once_with(
        betterstack_service.HeartbeatType.SERVER_INFO
    )


def test_character_collections_heartbeat_uses_global_service(monkeypatch):
    service = MagicMock()
    service.send_heartbeat.return_value = True
    monkeypatch.setattr(betterstack_service, "_betterstack_service", service)

    result = betterstack_service.character_collections_heartbeat()

    assert result is True
    service.send_heartbeat.assert_called_once_with(
        betterstack_service.HeartbeatType.CHARACTER_COLLECTIONS
    )


def test_lfm_collections_heartbeat_uses_global_service(monkeypatch):
    service = MagicMock()
    service.send_heartbeat.return_value = True
    monkeypatch.setattr(betterstack_service, "_betterstack_service", service)

    result = betterstack_service.lfm_collections_heartbeat()

    assert result is True
    service.send_heartbeat.assert_called_once_with(
        betterstack_service.HeartbeatType.LFM_COLLECTIONS
    )
