from business.alerts.channels.inbox import InboxChannel
from business.alerts.channels.web_push import WebPushChannel
from workers import alert_dispatch_worker


def test_build_dispatcher_uses_db_loader_and_rules_version(monkeypatch):
    monkeypatch.setenv("ALERT_RULE_REFRESH_SECS", "12")

    dispatcher = alert_dispatch_worker.build_dispatcher()

    assert dispatcher._load_rules is alert_dispatch_worker.get_enabled_rules_by_type
    assert dispatcher._refresh_seconds == 12.0
    assert dispatcher._get_rules_version is alert_dispatch_worker.read_rules_version


def test_web_push_channel_only_when_vapid_configured(monkeypatch):
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("VAPID_SUBJECT", raising=False)
    assert [type(c) for c in alert_dispatch_worker.build_channels()] == [InboxChannel]

    monkeypatch.setenv("VAPID_PRIVATE_KEY", "key")
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:test@example.com")
    assert [type(c) for c in alert_dispatch_worker.build_channels()] == [InboxChannel, WebPushChannel]
