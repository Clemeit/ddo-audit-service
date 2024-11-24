from typing import Optional

from pydantic import BaseModel


class PopulationDataPoint(BaseModel):
    character_count: int
    lfm_count: int


class PopulationPointInTime(BaseModel):
    timestamp: Optional[str]
    data: Optional[list[dict[str, PopulationDataPoint]]] = None


class GameWorld(BaseModel):
    name: str
    status_server: str
    order: int
    allow_billing_role: str = ""
    queue_number: int = 0
