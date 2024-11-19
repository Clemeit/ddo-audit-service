from pydantic import BaseModel

from typing import Optional
from datetime import datetime


class News(BaseModel):
    id: int
    message: str


class PageMessage(BaseModel):
    id: int
    message: str
    affected_pages: list[str]
    start_date: datetime
    end_date: datetime

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}
