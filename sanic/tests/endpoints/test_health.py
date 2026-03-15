import endpoints.health as health_endpoints


def test_health_check_returns_ok(make_request, run_async, response_json):
    request = make_request(path="/health")

    response = run_async(health_endpoints.health_check(request))

    assert response.status == 200
    assert response_json(response) == {"health": "ok"}
