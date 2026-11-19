"""Registry of alert types, keyed by name."""

from business.alerts.base import AlertType

_TYPES: dict[str, AlertType] = {}


def register(alert_type: AlertType) -> AlertType:
    if alert_type.name in _TYPES:
        raise ValueError(f"Alert type already registered: {alert_type.name}")
    _TYPES[alert_type.name] = alert_type
    return alert_type


def get_type(name: str) -> AlertType | None:
    return _TYPES.get(name)


def all_types() -> list[AlertType]:
    return list(_TYPES.values())


def user_creatable_types() -> list[AlertType]:
    return [t for t in _TYPES.values() if t.user_creatable]
