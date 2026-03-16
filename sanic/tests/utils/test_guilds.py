import pytest

from conftest import _amock
from constants.guilds import GUILD_NAME_MAX_LENGTH
import utils.guilds as guilds


class TestValidateGuildName:
    @pytest.mark.parametrize(
        "guild_name",
        [
            "The Silver Flame",
            "Argonnessen-Guard",
            "Guild.Name",
            "Guild's Keep",
            "A" * GUILD_NAME_MAX_LENGTH,
        ],
    )
    def test_accepts_valid_names(self, guild_name):
        assert guilds.validate_guild_name(guild_name) is True

    @pytest.mark.parametrize(
        "guild_name",
        [
            "",
            None,
            "A" * (GUILD_NAME_MAX_LENGTH + 1),
            "Invalid*Guild",
            "Bad!Name",
        ],
    )
    def test_rejects_invalid_names(self, guild_name):
        assert guilds.validate_guild_name(guild_name) is False


class TestGetCachedDataWithFallback:
    def test_returns_cached_data_when_present(self, monkeypatch):
        cached_data = [{"name": "GuildFromCache"}]
        set_calls = []

        monkeypatch.setattr(guilds.redis_client, "get_by_key", lambda key: cached_data)
        monkeypatch.setattr(
            guilds.redis_client,
            "set_by_key",
            lambda key, value, ttl=None: set_calls.append((key, value, ttl)),
        )

        result = guilds.get_cached_data_with_fallback(
            "all_guilds", lambda: [{"name": "GuildFromDb"}], ttl=123
        )

        assert result == cached_data
        assert set_calls == []

    def test_regenerates_and_caches_when_cache_miss(self, monkeypatch):
        set_calls = []
        fallback_calls = []

        monkeypatch.setattr(guilds.redis_client, "get_by_key", lambda key: None)
        monkeypatch.setattr(
            guilds.redis_client,
            "set_by_key",
            lambda key, value, ttl=None: set_calls.append((key, value, ttl)),
        )

        def _fallback():
            fallback_calls.append(True)
            return [{"name": "GuildFromDb"}]

        result = guilds.get_cached_data_with_fallback("all_guilds", _fallback, ttl=456)

        assert result == [{"name": "GuildFromDb"}]
        assert fallback_calls == [True]
        assert set_calls == [("all_guilds", [{"name": "GuildFromDb"}], 456)]


class TestGetAllGuilds:
    def test_uses_expected_cache_key_ttl_and_fallback(self, monkeypatch):
        captured = {}

        def _fake_get_cached_data_with_fallback(key, fallback_func, ttl):
            captured["key"] = key
            captured["fallback_func"] = fallback_func
            captured["ttl"] = ttl
            return [{"name": "MockGuild"}]

        monkeypatch.setattr(
            guilds,
            "get_cached_data_with_fallback",
            _fake_get_cached_data_with_fallback,
        )

        result = guilds.get_all_guilds()

        assert result == [{"name": "MockGuild"}]
        assert captured["key"] == "all_guilds"
        assert captured["ttl"] == guilds.UNIQUE_GUILDS_CACHE_TTL
        assert captured["fallback_func"] is guilds.postgres_client.get_all_guilds


class TestAsyncGetCachedDataWithFallback:
    def test_returns_cached_data_when_present(self, monkeypatch, run_async):
        cached_data = [{"name": "GuildFromCache"}]
        set_calls = []

        monkeypatch.setattr(
            guilds.redis_client,
            "async_get_by_key",
            _amock(lambda key: cached_data),
        )
        monkeypatch.setattr(
            guilds.redis_client,
            "async_set_by_key",
            _amock(lambda key, value, ttl=None: set_calls.append((key, value, ttl))),
        )

        result = run_async(
            guilds.async_get_cached_data_with_fallback(
                "all_guilds", _amock(lambda: [{"name": "GuildFromDb"}]), ttl=123
            )
        )

        assert result == cached_data
        assert set_calls == []

    def test_regenerates_and_caches_when_cache_miss(self, monkeypatch, run_async):
        set_calls = []
        fallback_calls = []

        monkeypatch.setattr(
            guilds.redis_client,
            "async_get_by_key",
            _amock(lambda key: None),
        )
        monkeypatch.setattr(
            guilds.redis_client,
            "async_set_by_key",
            _amock(lambda key, value, ttl=None: set_calls.append((key, value, ttl))),
        )

        async def _fallback():
            fallback_calls.append(True)
            return [{"name": "GuildFromDb"}]

        result = run_async(
            guilds.async_get_cached_data_with_fallback("all_guilds", _fallback, ttl=456)
        )

        assert result == [{"name": "GuildFromDb"}]
        assert fallback_calls == [True]
        assert set_calls == [("all_guilds", [{"name": "GuildFromDb"}], 456)]


class TestAsyncGetAllGuilds:
    def test_uses_expected_cache_key_ttl_and_fallback(self, monkeypatch, run_async):
        captured = {}

        async def _fake_async_cached(key, fallback_func, ttl):
            captured["key"] = key
            captured["fallback_func"] = fallback_func
            captured["ttl"] = ttl
            return [{"name": "MockGuild"}]

        monkeypatch.setattr(
            guilds,
            "async_get_cached_data_with_fallback",
            _fake_async_cached,
        )

        result = run_async(guilds.async_get_all_guilds())

        assert result == [{"name": "MockGuild"}]
        assert captured["key"] == "all_guilds"
        assert captured["ttl"] == guilds.UNIQUE_GUILDS_CACHE_TTL
        assert captured["fallback_func"] is guilds.postgres_client.async_get_all_guilds
