from pydantic import BaseModel


class News(BaseModel):
    id: int
    date: str
    message: str


class PageMessage(BaseModel):
    id: int
    message: str
    affected_pages: list[str]
    start_date: str
    end_date: str
