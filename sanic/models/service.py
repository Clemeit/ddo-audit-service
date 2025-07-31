from pydantic import BaseModel
from typing import Optional


class News(BaseModel):
    id: Optional[int] = None
    date: Optional[str] = None
    message: str


class PageMessage(BaseModel):
    id: Optional[int] = None
    message: str
    affected_pages: Optional[list[str]] = None
    dismissable: bool = False
    type: str = "info"  # 'info', 'warning', 'critical', or 'success'
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class FeedbackRequest(BaseModel):
    message: str
    contact: Optional[str] = None
    metadata: Optional[dict] = None
    user_id: Optional[str] = None


class LogRequest(BaseModel):
    # Core message
    message: str
    level: str  # "debug", "info", "warn", "error", "fatal"

    # Context
    timestamp: Optional[str] = None  # ISO 8601 format
    session_id: Optional[str] = None
    user_id: Optional[str] = None

    # Browser/Environment
    user_agent: Optional[str] = None
    browser: Optional[str] = None
    browser_version: Optional[str] = None
    os: Optional[str] = None
    screen_resolution: Optional[str] = None
    viewport_size: Optional[str] = None

    # Page/Application Context
    url: Optional[str] = None
    page_title: Optional[str] = None
    referrer: Optional[str] = None
    route: Optional[str] = None
    component: Optional[str] = None
    action: Optional[str] = None
    metadata: Optional[dict] = None

    # Network
    ip_address: Optional[str] = None
    country: Optional[str] = None

    # Additional fields
    is_internal: Optional[bool] = (
        False  # Indicates if the log is from an internal service or user
    )
