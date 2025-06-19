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
    start_date: Optional[str] = None
    end_date: Optional[str] = None
