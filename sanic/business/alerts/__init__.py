"""Alerts: rules, matching and delivery. Importing this package registers the built-in alert types.

To add an alert type: write ``types/<name>.py`` (subclass ``AlertType``), register it below, and call
``events.emit_alert_events("<name>", [...])`` where the data is produced. The worker, channels and
schema need no changes.
"""

from business.alerts import registry
from business.alerts.types.lfm_posted import LfmPostedAlertType
from business.alerts.types.test_alert import TestAlertType

if registry.get_type(LfmPostedAlertType.name) is None:
    registry.register(LfmPostedAlertType())
if registry.get_type(TestAlertType.name) is None:
    registry.register(TestAlertType())
