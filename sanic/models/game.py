from typing import Optional

from pydantic import BaseModel


class PopulationDataPoint(BaseModel):
    character_count: int
    lfm_count: int


class PopulationPointInTime(BaseModel):
    timestamp: float
    data: Optional[list[dict[str, PopulationDataPoint]]] = None
