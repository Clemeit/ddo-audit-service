"""Alert type ``friend_login``: one of the user's friends logged in.

A rule watches chosen characters, or all the user's friends (the friends list the site syncs to the
account). Event data (emitted by ``business/characters.py`` via ``producers.friend_login_events``)::

    {"character_id": 123, "name": "Somebody", "server_name": "thrane", "total_level": 30,
     "classes": [{"name": "Fighter", "level": 20}, ...]}
"""

from __future__ import annotations

from collections import defaultdict

from business.alerts.base import AlertRule, AlertType, RenderedAlert
from business.alerts.types._common import (
    UserContextLoader,
    default_user_context_loader,
    display_server,
    load_user_context,
    unique_positive_ids,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_CHARACTERS_PER_RULE = 50


class FriendLoginCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    all_friends: bool = False
    character_ids: list[int] = Field(
        default_factory=list, max_length=MAX_CHARACTERS_PER_RULE
    )

    @field_validator("character_ids")
    @classmethod
    def _unique(cls, value: list[int]) -> list[int]:
        return unique_positive_ids(value)

    @model_validator(mode="after")
    def _someone(self) -> "FriendLoginCriteria":
        if self.all_friends:
            self.character_ids = []
        elif not self.character_ids:
            raise ValueError("choose at least one friend, or all friends")
        return self


def _classes_text(classes) -> str:
    parts = []
    for c in classes or []:
        if isinstance(c, dict) and c.get("name"):
            level = c.get("level")
            parts.append(f"{c['name']} {level}" if level else str(c["name"]))
    return " / ".join(parts)


class FriendLoginAlertType(AlertType):
    name = "friend_login"
    label = "Friend logged in"
    Criteria = FriendLoginCriteria
    # Relogging (character swaps, crashes) alerts at most once in this window.
    dedupe_ttl_secs = 30 * 60

    def __init__(self, context_loader: UserContextLoader | None = None) -> None:
        self._context_loader = context_loader or default_user_context_loader

    def build_index(self, rules: list[AlertRule]) -> dict[int, list[AlertRule]]:
        context = load_user_context(
            self._context_loader,
            [r.user_id for r in rules if r.criteria.all_friends],
        )
        index: dict[int, list[AlertRule]] = defaultdict(list)
        for rule in rules:
            if rule.criteria.all_friends:
                ids = context.get(rule.user_id, {}).get("friends", set())
            else:
                ids = rule.criteria.character_ids
            for cid in ids:
                index[int(cid)].append(rule)
        return dict(index)

    def match(self, data: dict, index: dict[int, list[AlertRule]]) -> list[AlertRule]:
        cid = data.get("character_id")
        if not isinstance(cid, int):
            return []
        return list((index or {}).get(cid, []))

    def dedupe_key(self, data: dict) -> str:
        return f"friend:{data.get('character_id')}"

    def render(self, data: dict, rule: AlertRule) -> RenderedAlert:
        server_lower = str(data.get("server_name") or "").lower()
        server = display_server(server_lower)
        name = data.get("name") or "A friend"
        details = []
        if data.get("total_level"):
            details.append(f"Level {data['total_level']}")
        classes = _classes_text(data.get("classes"))
        if classes:
            details.append(classes)
        details.append(f"on {server}")
        return RenderedAlert(
            type=self.name,
            rule_id=rule.id,
            title=f"{name} logged in",
            body=" · ".join(details[:-1] + [details[-1]]) if details else server,
            url="/friends",
            tag=f"friend-{name.lower()}-{server_lower}",
        )
