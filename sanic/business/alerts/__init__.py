"""Alerts: rules, matching and delivery. Importing this package registers the built-in alert types.

To add an alert type: write ``types/<name>.py`` (subclass ``AlertType``), register it below, and call
``events.emit_alert_events("<name>", [...])`` where the data is produced (see ``producers.py``). The
worker, channels and schema need no changes.
"""

from business.alerts import registry
from business.alerts.types.friend_login import FriendLoginAlertType
from business.alerts.types.lfm_posted import LfmPostedAlertType
from business.alerts.types.server_status import ServerStatusAlertType
from business.alerts.types.shard_exchange_listed import ShardExchangeListedAlertType
from business.alerts.types.shard_exchange_outbid import ShardExchangeOutbidAlertType
from business.alerts.types.test_alert import TestAlertType

for _alert_type in (
    LfmPostedAlertType(),
    ServerStatusAlertType(),
    FriendLoginAlertType(),
    ShardExchangeListedAlertType(),
    ShardExchangeOutbidAlertType(),
    TestAlertType(),
):
    if registry.get_type(_alert_type.name) is None:
        registry.register(_alert_type)
