"""Alert type ``server_status``: a server went offline or came back online.

Event data (emitted by ``endpoints/game.py`` via ``producers.server_status_events``)::

    {"server_name": "thrane", "is_online": false}
"""

from __future__ import annotations

from collections import defaultdict

from business.alerts.base import AlertRule, AlertType, RenderedAlert
from business.alerts.types._common import (
    canonical_servers,
    display_server,
)
from constants.server import SERVER_NAMES
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ANY_SERVER = "*"


class ServerStatusCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Empty means any server.
    server_names: list[str] = Field(default_factory=list, max_length=len(SERVER_NAMES))
    on_offline: bool = True
    on_online: bool = True

    @field_validator("server_names")
    @classmethod
    def _known_servers(cls, value: list[str]) -> list[str]:
        return canonical_servers(value)

    @model_validator(mode="after")
    def _some_direction(self) -> "ServerStatusCriteria":
        if not self.on_offline and not self.on_online:
            raise ValueError("choose going offline, coming online, or both")
        return self


class ServerStatusAlertType(AlertType):
    name = "server_status"
    label = "Server status"
    Criteria = ServerStatusCriteria
    # A flapping server alerts at most once per direction in this window.
    dedupe_ttl_secs = 15 * 60

    def build_index(self, rules: list[AlertRule]) -> dict[str, list[AlertRule]]:
        index: dict[str, list[AlertRule]] = defaultdict(list)
        for rule in rules:
            for server in rule.criteria.server_names or [ANY_SERVER]:
                index[server.lower()].append(rule)
        return dict(index)

    def match(self, data: dict, index: dict[str, list[AlertRule]]) -> list[AlertRule]:
        server = str(data.get("server_name") or "").lower()
        is_online = data.get("is_online")
        if not server or not isinstance(is_online, bool):
            return []
        candidates = (index or {}).get(server, []) + (index or {}).get(ANY_SERVER, [])
        return [
            r
            for r in candidates
            if (r.criteria.on_online if is_online else r.criteria.on_offline)
        ]

    def dedupe_key(self, data: dict) -> str:
        state = "online" if data.get("is_online") else "offline"
        return f"server:{str(data.get('server_name') or '').lower()}:{state}"

    def render(self, data: dict, rule: AlertRule) -> RenderedAlert:
        server_lower = str(data.get("server_name") or "").lower()
        server = display_server(server_lower)
        online = bool(data.get("is_online"))
        return RenderedAlert(
            type=self.name,
            rule_id=rule.id,
            title=f"{server} is back online" if online else f"{server} is offline",
            body=(
                "The server is accepting players again."
                if online
                else "The server stopped responding."
            ),
            url=f"/servers/{server_lower}",
            tag=f"server-{server_lower}",
        )
