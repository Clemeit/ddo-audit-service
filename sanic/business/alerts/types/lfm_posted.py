"""Alert type ``lfm_posted``: an LFM for one of the user's quests was posted (or switched to that quest).

Event data (emitted by ``business/lfms.py``)::

    {"server_name": "argonnessen", "lfm_id": 123, "quest_id": 456, "difficulty": "Elite",
     "minimum_level": 30, "maximum_level": 34, "leader_name": "Somebody", "member_count": 3}
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable, Optional

from business.alerts.base import AlertRule, AlertType, RenderedAlert
from constants.server import SERVER_NAMES
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

DIFFICULTIES: list[str] = [
    "Casual",
    "Normal",
    "Hard",
    "Elite",
    "Reaper",
    "Epic Casual",
    "Epic Normal",
    "Epic Hard",
    "Epic Elite",
    "Epic Reaper",
]
_DIFFICULTY_BY_LOWER = {d.lower(): d for d in DIFFICULTIES}
_SERVER_BY_LOWER = {s.lower(): s for s in SERVER_NAMES}

MAX_QUESTS_PER_RULE = 20
MIN_LEVEL = 1
MAX_LEVEL = 50


class LfmPostedCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quest_ids: list[int] = Field(min_length=1, max_length=MAX_QUESTS_PER_RULE)
    # Empty means any server / any difficulty.
    server_names: list[str] = Field(default_factory=list, max_length=len(SERVER_NAMES))
    min_level: Optional[int] = Field(default=None, ge=MIN_LEVEL, le=MAX_LEVEL)
    max_level: Optional[int] = Field(default=None, ge=MIN_LEVEL, le=MAX_LEVEL)
    difficulties: list[str] = Field(default_factory=list, max_length=len(DIFFICULTIES))

    @field_validator("quest_ids")
    @classmethod
    def _unique_positive_quests(cls, value: list[int]) -> list[int]:
        if any(q <= 0 for q in value):
            raise ValueError("quest IDs must be positive")
        return list(dict.fromkeys(value))

    @field_validator("server_names")
    @classmethod
    def _known_servers(cls, value: list[str]) -> list[str]:
        out = []
        for name in value:
            canonical = _SERVER_BY_LOWER.get(str(name).strip().lower())
            if not canonical:
                raise ValueError(f"unknown server: {name}")
            out.append(canonical)
        return list(dict.fromkeys(out))

    @field_validator("difficulties")
    @classmethod
    def _known_difficulties(cls, value: list[str]) -> list[str]:
        out = []
        for name in value:
            canonical = _DIFFICULTY_BY_LOWER.get(str(name).strip().lower())
            if not canonical:
                raise ValueError(f"unknown difficulty: {name}")
            out.append(canonical)
        return list(dict.fromkeys(out))

    @model_validator(mode="after")
    def _level_range(self) -> "LfmPostedCriteria":
        if (
            self.min_level is not None
            and self.max_level is not None
            and self.min_level > self.max_level
        ):
            raise ValueError("min_level must not be greater than max_level")
        return self


def criteria_matches(criteria: LfmPostedCriteria, data: dict) -> bool:
    """Does one rule's criteria match the event data? (Quest membership is checked by the index.)"""
    quest_id = data.get("quest_id")
    if not quest_id or quest_id not in criteria.quest_ids:
        return False

    if criteria.server_names:
        server = str(data.get("server_name") or "").lower()
        if server not in {s.lower() for s in criteria.server_names}:
            return False

    # Level ranges overlap. An LFM with no level bound on a side is open on that side.
    lfm_min = data.get("minimum_level")
    lfm_max = data.get("maximum_level")
    if criteria.max_level is not None and lfm_min is not None:
        if lfm_min > criteria.max_level:
            return False
    if criteria.min_level is not None and lfm_max is not None:
        if lfm_max < criteria.min_level:
            return False

    if criteria.difficulties:
        difficulty = str(data.get("difficulty") or "").lower()
        if difficulty not in {d.lower() for d in criteria.difficulties}:
            return False

    return True


def _default_quest_name_loader() -> dict[int, str]:
    from services.postgres import get_all_quests  # imported lazily: needs a DB

    return {q.id: q.name for q in get_all_quests() if q.name}


class LfmPostedAlertType(AlertType):
    name = "lfm_posted"
    label = "LFM posted"
    Criteria = LfmPostedCriteria

    def __init__(
        self, quest_name_loader: Callable[[], dict[int, str]] | None = None
    ) -> None:
        self._quest_name_loader = quest_name_loader or _default_quest_name_loader
        self._quest_names: dict[int, str] = {}

    def refresh(self) -> None:
        try:
            names = self._quest_name_loader()
            if names:
                self._quest_names = names
        except Exception as e:
            logger.warning(f"Could not load quest names (keeping old ones): {e}")

    def build_index(self, rules: list[AlertRule]) -> dict[int, list[AlertRule]]:
        index: dict[int, list[AlertRule]] = defaultdict(list)
        for rule in rules:
            for quest_id in rule.criteria.quest_ids:
                index[quest_id].append(rule)
        return dict(index)

    def match(self, data: dict, index: dict[int, list[AlertRule]]) -> list[AlertRule]:
        quest_id = data.get("quest_id")
        if not quest_id:
            return []
        return [
            r for r in index.get(quest_id, []) if criteria_matches(r.criteria, data)
        ]

    @staticmethod
    def _stable_id(data: dict):
        return data.get("leader_id") or data.get("lfm_id")

    def dedupe_key(self, data: dict) -> str:
        server = str(data.get("server_name") or "").lower()
        return f"lfm:{server}:{self._stable_id(data)}:{data.get('quest_id')}"

    def render(self, data: dict, rule: AlertRule) -> RenderedAlert:
        server_lower = str(data.get("server_name") or "").lower()
        server = _SERVER_BY_LOWER.get(server_lower, server_lower.title())
        quest_id = data.get("quest_id")
        quest_name = (
            data.get("quest_name") or self._quest_names.get(quest_id) or "A quest"
        )

        details = []
        if data.get("leader_name"):
            details.append(str(data["leader_name"]))
        lfm_min, lfm_max = data.get("minimum_level"), data.get("maximum_level")
        if lfm_min is not None and lfm_max is not None:
            details.append(f"Level {lfm_min}-{lfm_max}")
        if data.get("difficulty"):
            details.append(str(data["difficulty"]))
        if data.get("member_count"):
            count = int(data["member_count"])
            details.append(f"{count} member{'s' if count != 1 else ''}")

        return RenderedAlert(
            type=self.name,
            rule_id=rule.id,
            title=f"{quest_name} on {server}",
            body=" · ".join(details) or "New LFM posted",
            url=f"/grouping/{server_lower}",
            tag=f"lfm-{server_lower}-{self._stable_id(data)}",
        )
