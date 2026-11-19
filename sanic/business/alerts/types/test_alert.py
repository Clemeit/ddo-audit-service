"""Alert type ``test``: sent by "Send test alert" so a user can check the whole pipeline end to end.

Event data: ``{"user_id": 1, "nonce": "<random>"}``. Users cannot create rules for it.
"""

from business.alerts.base import AlertRule, AlertType, RenderedAlert


class TestAlertType(AlertType):
    __test__ = False  # not a pytest test class

    name = "test"
    label = "Test alert"
    Criteria = None
    user_creatable = False

    def build_index(self, rules):
        return None

    def match(self, data: dict, index) -> list[AlertRule]:
        user_id = data.get("user_id")
        if not isinstance(user_id, int) or user_id <= 0:
            return []
        return [AlertRule(id=0, user_id=user_id, type=self.name, criteria=None)]

    def dedupe_key(self, data: dict) -> str:
        return f"test:{data.get('nonce')}"

    def render(self, data: dict, rule: AlertRule) -> RenderedAlert:
        return RenderedAlert(
            type=self.name,
            rule_id=0,
            title="Test alert",
            body="Your DDO Audit alerts are working.",
            url="/notifications",
            tag=f"test-{data.get('nonce')}",
        )
