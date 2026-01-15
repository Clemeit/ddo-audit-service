from typing import Optional
from datetime import datetime

from pydantic import BaseModel, Field


class QuestSession(BaseModel):
    """Model for quest session data from the database."""

    id: Optional[int] = None
    character_id: int
    quest_id: int
    entry_timestamp: datetime
    exit_timestamp: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    created_at: Optional[datetime] = None


class QuestAnalytics(BaseModel):
    """Model for quest analytics response."""

    average_duration_seconds: Optional[float] = None
    standard_deviation_seconds: Optional[float] = None
    histogram: list[dict] = Field(default_factory=list)  # [{bin_start, bin_end, count}]
    activity_by_hour: list[dict] = Field(default_factory=list)  # [{hour: 0-23, count}]
    activity_by_day_of_week: list[dict] = Field(
        default_factory=list
    )  # [{day: 0-6, day_name, count}]
    activity_over_time: list[dict] = Field(
        default_factory=list
    )  # [{date: YYYY-MM-DD, count}]
    total_sessions: int = 0
    completed_sessions: int = 0
    active_sessions: int = 0


class UnprocessedQuestSessionActivity(BaseModel):
    """Model for unprocessed quest-session-relevant activities (location + status)."""

    character_id: int
    timestamp: datetime
    activity_type: str  # "location" or "status"
    area_id: Optional[int] = None  # Only set if activity_type == "location"
    is_online: Optional[bool] = None  # Only set if activity_type == "status"
    ctid_text: str  # PostgreSQL tuple ID for row identification


class QuestMetrics(BaseModel):
    """Model for cached quest metrics with relative performance scores."""

    quest_id: int
    heroic_xp_per_minute_relative: Optional[float] = None  # 0-1 normalized
    epic_xp_per_minute_relative: Optional[float] = None  # 0-1 normalized
    popularity_relative: Optional[float] = None  # 0-1 normalized
    analytics_data: QuestAnalytics
    updated_at: datetime
