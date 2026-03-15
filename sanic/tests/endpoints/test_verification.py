from types import SimpleNamespace

import endpoints.verification as verification_endpoints


def _character(*, is_online, is_anonymous, public_comment):
    return SimpleNamespace(
        is_online=is_online,
        is_anonymous=is_anonymous,
        public_comment=public_comment,
    )


def test_get_verification_challenge_returns_defaults_when_character_missing(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        verification_endpoints,
        "get_challenge_word_for_character_by_character_id",
        lambda _character_id: "fern",
    )
    monkeypatch.setattr(
        verification_endpoints.redis_client,
        "get_character_by_id",
        lambda _character_id: None,
    )

    request = make_request(path="/v1/verification/25")
    response = run_async(verification_endpoints.get_verification_challenge(request, 25))

    assert response.status == 200
    data = response_json(response)["data"]
    assert data["challenge_word"] == "fern"
    assert data["is_online"] is False
    assert data["is_anonymous"] is True
    assert data["challenge_word_match"] is False
    assert data["challenge_passed"] is False
    assert data["access_token"] == ""


def test_get_verification_challenge_passes_with_existing_access_token(
    monkeypatch, make_request, run_async, response_json
):
    saved = {"called": False}

    monkeypatch.setattr(
        verification_endpoints,
        "get_challenge_word_for_character_by_character_id",
        lambda _character_id: "fern",
    )
    monkeypatch.setattr(
        verification_endpoints.redis_client,
        "get_character_by_id",
        lambda _character_id: _character(
            is_online=True, is_anonymous=False, public_comment="fern"
        ),
    )
    monkeypatch.setattr(
        verification_endpoints.postgres_client,
        "get_access_token_by_character_id",
        lambda _character_id: "existing-token",
    )
    monkeypatch.setattr(
        verification_endpoints.postgres_client,
        "save_access_token",
        lambda _character_id, _token: saved.update({"called": True}),
    )

    request = make_request(path="/v1/verification/26")
    response = run_async(verification_endpoints.get_verification_challenge(request, 26))

    assert response.status == 200
    data = response_json(response)["data"]
    assert data["challenge_passed"] is True
    assert data["access_token"] == "existing-token"
    assert saved["called"] is False


def test_get_verification_challenge_creates_access_token_when_missing(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    monkeypatch.setattr(
        verification_endpoints,
        "get_challenge_word_for_character_by_character_id",
        lambda _character_id: "fern",
    )
    monkeypatch.setattr(
        verification_endpoints.redis_client,
        "get_character_by_id",
        lambda _character_id: _character(
            is_online=True, is_anonymous=False, public_comment="fern"
        ),
    )
    monkeypatch.setattr(
        verification_endpoints.postgres_client,
        "get_access_token_by_character_id",
        lambda _character_id: "",
    )
    monkeypatch.setattr(
        verification_endpoints.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="generated-token"),
    )

    def _save_access_token(character_id, access_token):
        captured["saved"] = (character_id, access_token)

    monkeypatch.setattr(
        verification_endpoints.postgres_client,
        "save_access_token",
        _save_access_token,
    )

    request = make_request(path="/v1/verification/27")
    response = run_async(verification_endpoints.get_verification_challenge(request, 27))

    assert response.status == 200
    data = response_json(response)["data"]
    assert data["challenge_passed"] is True
    assert data["access_token"] == "generated-token"
    assert captured["saved"] == (27, "generated-token")


def test_get_verification_challenge_returns_500_on_unexpected_error(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        verification_endpoints,
        "get_challenge_word_for_character_by_character_id",
        lambda _character_id: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    request = make_request(path="/v1/verification/99")
    response = run_async(verification_endpoints.get_verification_challenge(request, 99))

    assert response.status == 500
    assert response_json(response)["message"] == "boom"
