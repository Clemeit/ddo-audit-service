from business.alerts.channels.inbox import InboxChannel
from workers import alert_dispatch_worker


def test_build_dispatcher_uses_inbox_channel_and_db_loader(monkeypatch):
    monkeypatch.setenv("ALERT_RULE_REFRESH_SECS", "12")

    dispatcher = alert_dispatch_worker.build_dispatcher()

    assert [type(c) for c in dispatcher.channels] == [InboxChannel]
    assert dispatcher._load_rules is alert_dispatch_worker.get_enabled_rules_by_type
    assert dispatcher._refresh_seconds == 12.0
    assert dispatcher._get_rules_version is alert_dispatch_worker.read_rules_version
