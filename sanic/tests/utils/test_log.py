from datetime import datetime

from models.service import LogRequest
from utils.log import logMessage


class TestLogMessage:
    def test_persists_log_request_with_context(self, monkeypatch):
        captured = {}

        def fake_persist_log(log_request):
            captured["log_request"] = log_request

        monkeypatch.setattr("utils.log.postgres_client.persist_log", fake_persist_log)

        logMessage(
            "job started",
            level="warn",
            user_id="user-1",
            component="worker",
        )

        log_request = captured["log_request"]
        assert isinstance(log_request, LogRequest)
        assert log_request.message == "job started"
        assert log_request.level == "warn"
        assert log_request.user_id == "user-1"
        assert log_request.component == "worker"
        assert log_request.is_internal is True
        assert log_request.timestamp is not None
        assert isinstance(datetime.fromisoformat(log_request.timestamp), datetime)

    def test_uses_default_info_level(self, monkeypatch):
        captured = {}

        def fake_persist_log(log_request):
            captured["log_request"] = log_request

        monkeypatch.setattr("utils.log.postgres_client.persist_log", fake_persist_log)

        logMessage("hello")

        assert captured["log_request"].level == "info"

    def test_handles_model_validation_errors(self, monkeypatch):
        persist_calls = []
        printed = []

        monkeypatch.setattr(
            "utils.log.postgres_client.persist_log",
            lambda log_request: persist_calls.append(log_request),
        )
        monkeypatch.setattr(
            "builtins.print", lambda *args, **kwargs: printed.append(args[0])
        )

        logMessage(None)

        assert persist_calls == []
        assert len(printed) == 1
        assert "Failed to create log request" in printed[0]

    def test_handles_persist_exceptions(self, monkeypatch):
        printed = []

        def fake_persist_log(_):
            raise RuntimeError("db unavailable")

        monkeypatch.setattr("utils.log.postgres_client.persist_log", fake_persist_log)
        monkeypatch.setattr(
            "builtins.print", lambda *args, **kwargs: printed.append(args[0])
        )

        logMessage("will fail")

        assert len(printed) == 1
        assert "Failed to create log request" in printed[0]
        assert "db unavailable" in printed[0]
