from datetime import datetime
from models.base_model import ConfiguredBaseModel as BaseModel


class News(BaseModel):
    id: int
    message: str


class PageMessage(BaseModel):
    id: int
    message: str
    affected_pages: list[str]
    start_date: datetime
    end_date: datetime
