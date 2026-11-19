"""Building blocks shared by every alert type and channel.

An alert *type* (e.g. ``lfm_posted``) knows how to validate a rule's criteria, index the enabled rules,
match an incoming event against that index, and render a match into a :class:`RenderedAlert`.
A *channel* (in-tab inbox, web push, Discord, ...) delivers a rendered alert to a user. The dispatcher in
``business/alerts/dispatcher.py`` ties the two together, so adding a type never touches the channels and
adding a channel never touches the types.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

from pydantic import BaseModel


@dataclass(frozen=True)
class AlertRule:
    """An enabled rule as the dispatcher sees it (criteria already validated)."""

    id: int
    user_id: int
    type: str
    criteria: BaseModel | None
    expires_at: datetime | None = None

    def is_active(self, now: datetime) -> bool:
        return self.expires_at is None or self.expires_at > now


@dataclass
class RenderedAlert:
    """What every channel delivers. ``url`` is site-relative; ``tag`` collapses duplicates."""

    type: str
    rule_id: int
    title: str
    body: str
    url: str
    tag: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


class AlertType:
    """Base class for alert types. Subclass, fill in the class attributes and methods, then register."""

    name: ClassVar[str]
    label: ClassVar[str]
    Criteria: ClassVar[type[BaseModel] | None] = None
    # False for internal types (e.g. the test alert) that users cannot create rules for.
    user_creatable: ClassVar[bool] = True

    def refresh(self) -> None:
        """Reload any lookup data the type needs (called by the worker alongside the rule refresh)."""

    def build_index(self, rules: list[AlertRule]) -> Any:
        """Return a structure that makes ``match`` cheap. Rules are enabled and validated."""
        return rules

    def match(self, data: dict, index: Any) -> list[AlertRule]:
        raise NotImplementedError

    def render(self, data: dict, rule: AlertRule) -> RenderedAlert:
        raise NotImplementedError

    def dedupe_key(self, data: dict) -> str:
        """Identity of the event; the dispatcher prefixes it with the user ID."""
        raise NotImplementedError


class AlertChannel:
    """Base class for delivery channels."""

    name: ClassVar[str]

    def deliver(self, client: Any, user_id: int, alert: RenderedAlert) -> bool:
        """Deliver to one user. ``client`` is a sync Redis client. Return True if something was sent."""
        raise NotImplementedError
