from types import SimpleNamespace

from utils.route import is_jwt_protected, is_method_open, is_route_open


def _request(method: str, path: str):
    return SimpleNamespace(method=method, path=path)


class TestIsMethodOpen:
    def test_get_is_open(self):
        assert is_method_open(_request("GET", "/any")) is True

    def test_post_is_not_open(self):
        assert is_method_open(_request("POST", "/any")) is False


class TestIsRouteOpen:
    def test_open_route_with_version_prefix_matches(self):
        assert is_route_open(_request("POST", "/v1/auth/login")) is True

    def test_open_route_without_version_prefix_matches(self):
        assert is_route_open(_request("POST", "/service/log")) is True

    def test_route_pattern_with_parameter_matches(self):
        assert is_route_open(_request("GET", "/v2/user/settings/player-1")) is True

    def test_method_mismatch_does_not_match(self):
        assert is_route_open(_request("GET", "/v1/auth/login")) is False

    def test_unknown_route_is_not_open(self):
        assert is_route_open(_request("POST", "/v1/not/open")) is False


class TestIsJwtProtected:
    def test_user_profile_is_jwt_protected(self):
        assert is_jwt_protected(_request("GET", "/v1/user/profile")) is True

    def test_user_password_change_is_jwt_protected(self):
        assert is_jwt_protected(_request("POST", "/v1/user/profile/password")) is True

    def test_logout_is_jwt_protected(self):
        assert is_jwt_protected(_request("POST", "/v3/auth/logout")) is True

    def test_delete_account_is_jwt_protected(self):
        assert is_jwt_protected(_request("DELETE", "/v2/auth/account")) is True

    def test_non_jwt_route_is_not_protected(self):
        assert is_jwt_protected(_request("GET", "/auth/login")) is False
