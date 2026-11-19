from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

MAX_ALERT_RULE_BODY_BYTES = 8 * 1024


class AlertRuleInput(BaseModel):
    """Body of POST/PUT /v1/user/alerts/rules. ``criteria`` is validated by the type's own model."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=64)
    criteria: dict = Field(default_factory=dict)
    enabled: bool = True
    expires_at: Optional[datetime] = None
