from types import SimpleNamespace

import endpoints.service as service_endpoints


def _model_with_dump(**data):
    return SimpleNamespace(model_dump=lambda: data)


def test_clamp_int_bounds_values():
    assert service_endpoints._clamp_int("9999", 60, min_value=1, max_value=1440) == 1440
    assert service_endpoints._clamp_int("0", 60, min_value=1, max_value=1440) == 1
    assert service_endpoints._clamp_int("bad", 60, min_value=1, max_value=1440) == 60


def test_get_health_returns_combined_health_status(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(service_endpoints.postgres_client, "health_check", lambda: True)
    monkeypatch.setattr(
        service_endpoints.redis_client, "redis_health_check", lambda: False
    )

    request = make_request(path="/v1/service/health")
    response = run_async(service_endpoints.get_health(request))

    assert response.status == 200
    payload = response_json(response)["data"]
    assert payload["postgres"] is True
    assert payload["redis"] is False


def test_get_news_serializes_model_results(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        service_endpoints.postgres_client,
        "get_news",
        lambda: [
            _model_with_dump(id=1, message="Hello"),
            _model_with_dump(id=2, message="World"),
        ],
    )

    request = make_request(path="/v1/service/news")
    response = run_async(service_endpoints.get_news(request))

    assert response.status == 200
    data = response_json(response)["data"]
    assert data[0]["message"] == "Hello"
    assert data[1]["id"] == 2


def test_get_page_message_by_page_delegates_to_postgres(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    def _get_page_messages(page_name):
        captured["page_name"] = page_name
        return [_model_with_dump(page_name=page_name, message="banner")]

    monkeypatch.setattr(
        service_endpoints.postgres_client, "get_page_messages", _get_page_messages
    )

    request = make_request(path="/v1/service/page_messages/home")
    response = run_async(service_endpoints.get_page_message_by_page(request, "home"))

    assert response.status == 200
    assert captured["page_name"] == "home"
    assert response_json(response)["data"][0]["page_name"] == "home"


def test_post_feedback_returns_400_for_invalid_body(
    make_request, run_async, response_json
):
    request = make_request(method="POST", path="/v1/service/feedback", json_body={})
    response = run_async(service_endpoints.post_feedback(request))

    assert response.status == 400
    assert response_json(response)["message"] == "improperly formatted body"


def test_post_feedback_returns_400_for_overlong_message(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        service_endpoints.FeedbackRequest,
        "model_validate",
        lambda _body: SimpleNamespace(message="x" * 5001, contact="contact"),
    )

    request = make_request(
        method="POST",
        path="/v1/service/feedback",
        json_body={"message": "ignored by monkeypatch"},
    )
    response = run_async(service_endpoints.post_feedback(request))

    assert response.status == 400
    assert response_json(response)["message"] == "feedback message too long"


def test_post_feedback_generates_ticket_and_persists(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    monkeypatch.setattr(
        service_endpoints.FeedbackRequest,
        "model_validate",
        lambda _body: SimpleNamespace(message="good", contact="me@example.com"),
    )
    monkeypatch.setattr(
        service_endpoints.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="ticket-123"),
    )

    def _post_feedback(feedback, ticket):
        captured["feedback"] = feedback
        captured["ticket"] = ticket

    monkeypatch.setattr(
        service_endpoints.postgres_client, "post_feedback", _post_feedback
    )

    request = make_request(
        method="POST",
        path="/v1/service/feedback",
        json_body={"message": "hello", "contact": "me@example.com"},
    )
    response = run_async(service_endpoints.post_feedback(request))

    assert response.status == 200
    assert response_json(response)["data"]["ticket"] == "ticket-123"
    assert captured["ticket"] == "ticket-123"


def test_post_log_uses_forwarded_ip_and_persists(monkeypatch, make_request, run_async):
    captured = {}

    monkeypatch.setattr(
        service_endpoints.LogRequest,
        "model_validate",
        lambda _body: SimpleNamespace(ip_address="", message="m"),
    )

    def _persist(log):
        captured["ip_address"] = log.ip_address

    monkeypatch.setattr(service_endpoints.postgres_client, "persist_log", _persist)

    request = make_request(
        method="POST",
        path="/v1/service/log",
        json_body={"message": "ok"},
        headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"},
        ip="127.0.0.1",
    )
    response = run_async(service_endpoints.post_log(request))

    assert response.status == 204
    assert captured["ip_address"] == "203.0.113.9"


def test_post_traffic_top_ips_rejects_invalid_metric(
    make_request, run_async, response_json
):
    request = make_request(
        method="POST",
        path="/v1/service/traffic/top_ips",
        json_body={"metric": "latency"},
    )
    request.args = {}

    response = run_async(service_endpoints.post_traffic_top_ips(request))

    assert response.status == 400
    assert "metric must be" in response_json(response)["message"]


def test_post_traffic_top_ips_clamps_values_and_awaits_redis(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    async def _traffic_top_ips(*, minutes, metric, limit):
        captured["minutes"] = minutes
        captured["metric"] = metric
        captured["limit"] = limit
        return [{"ip": "203.0.113.9", "requests": 10}]

    monkeypatch.setattr(
        service_endpoints.redis_client, "traffic_top_ips", _traffic_top_ips
    )

    request = make_request(
        method="POST",
        path="/v1/service/traffic/top_ips",
        json_body={"minutes": 99999, "limit": -1, "metric": "REQUESTS"},
    )
    request.args = {}

    response = run_async(service_endpoints.post_traffic_top_ips(request))

    assert response.status == 200
    assert captured["minutes"] == 1440
    assert captured["limit"] == 1
    assert captured["metric"] == "requests"
    assert response_json(response)["meta"]["metric"] == "requests"


def test_post_traffic_top_routes_uses_query_params_when_body_empty(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    async def _traffic_top_routes(*, minutes, metric, limit):
        captured["minutes"] = minutes
        captured["metric"] = metric
        captured["limit"] = limit
        return [{"route": "/v1/quests", "requests": 7}]

    monkeypatch.setattr(
        service_endpoints.redis_client, "traffic_top_routes", _traffic_top_routes
    )

    request = make_request(
        method="POST", path="/v1/service/traffic/top_routes", json_body=None
    )
    request.args = {"minutes": "30", "limit": "12", "metric": "bytes_out"}

    response = run_async(service_endpoints.post_traffic_top_routes(request))

    assert response.status == 200
    assert captured == {"minutes": 30, "metric": "bytes_out", "limit": 12}
    assert response_json(response)["data"][0]["route"] == "/v1/quests"


def test_delete_cache_key_returns_500_when_expire_fails(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        service_endpoints.redis_client,
        "expire_key_immediately",
        lambda _key: (_ for _ in ()).throw(RuntimeError("redis unavailable")),
    )

    request = make_request(method="DELETE", path="/v1/service/cache/key")
    response = run_async(service_endpoints.delete_cache_key(request, "my-key"))

    assert response.status == 500
    assert (
        response_json(response)["message"]
        == "A failure occurred expiring the Redis key."
    )
