import pytest
from pydantic import ValidationError

from models.user import (
    ChangePassword,
    ChangePasswordResponse,
    RefreshTokenRequest,
    RefreshTokenResponse,
    UserAuthResponse,
    UserLogin,
    UserProfile,
    UserRegister,
    UserSettings,
)


def test_user_register_valid_and_model_dump():
    model = UserRegister(username="User123", password="Pass!123")

    assert model.model_dump() == {
        "username": "User123",
        "password": "Pass!123",
    }


@pytest.mark.parametrize(
    "payload", [{}, {"username": "validuser"}, {"password": "Pass!123"}]
)
def test_user_register_missing_required_fields(payload):
    with pytest.raises(ValidationError):
        UserRegister(**payload)


@pytest.mark.parametrize("username", ["bad_name", "bad name", "name+"])
def test_user_register_rejects_non_alphanumeric_username(username):
    with pytest.raises(ValidationError):
        UserRegister(username=username, password="Pass!123")


def test_user_register_rejects_password_equal_to_username():
    with pytest.raises(ValidationError):
        UserRegister(username="SameName", password="SameName")


def test_user_register_rejects_password_with_invalid_characters():
    with pytest.raises(ValidationError):
        UserRegister(username="ValidUser", password="bad password")


def test_user_login_and_profile_models():
    login = UserLogin(username="alice", password="secret")
    profile = UserProfile(id=1, username="alice", created_at="2026-03-15T12:00:00Z")

    assert login.model_dump() == {"username": "alice", "password": "secret"}
    assert profile.model_dump() == {
        "id": 1,
        "username": "alice",
        "created_at": "2026-03-15T12:00:00Z",
    }


@pytest.mark.parametrize("payload", [{"username": "alice"}, {"password": "secret"}])
def test_user_login_missing_required_fields(payload):
    with pytest.raises(ValidationError):
        UserLogin(**payload)


def test_refresh_token_request_validation():
    assert RefreshTokenRequest(refresh_token="token-1").refresh_token == "token-1"

    with pytest.raises(ValidationError):
        RefreshTokenRequest(refresh_token="")


def test_change_password_valid_and_invalid():
    model = ChangePassword(old_password="old-pass", new_password="NewPass!9")
    assert model.model_dump() == {
        "old_password": "old-pass",
        "new_password": "NewPass!9",
    }

    with pytest.raises(ValidationError):
        ChangePassword(old_password="old-pass", new_password="bad pass")

    with pytest.raises(ValidationError):
        ChangePassword(old_password="", new_password="NewPass!9")


def test_user_settings_default_and_type_validation():
    settings = UserSettings()
    assert settings.model_dump() == {"settings": {}}

    with pytest.raises(ValidationError):
        UserSettings(settings=["not", "a", "dict"])


def test_auth_response_defaults_and_model_dump():
    profile = UserProfile(id=7, username="user7", created_at="2026-03-15T12:00:00Z")

    auth_response = UserAuthResponse(
        access_token="access",
        refresh_token="refresh",
        user=profile,
    )
    refresh_response = RefreshTokenResponse(
        access_token="access2",
        refresh_token="refresh2",
    )
    password_response = ChangePasswordResponse(
        access_token="access3",
        refresh_token="refresh3",
        message="Password changed",
    )

    assert auth_response.token_type == "Bearer"
    assert auth_response.expires_in == 900
    assert auth_response.refresh_expires_in == 2592000
    assert auth_response.model_dump()["user"]["username"] == "user7"

    assert refresh_response.model_dump() == {
        "access_token": "access2",
        "refresh_token": "refresh2",
        "token_type": "Bearer",
        "expires_in": 900,
        "refresh_expires_in": 2592000,
    }

    assert password_response.model_dump() == {
        "access_token": "access3",
        "refresh_token": "refresh3",
        "token_type": "Bearer",
        "expires_in": 900,
        "refresh_expires_in": 2592000,
        "message": "Password changed",
    }
