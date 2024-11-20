from typing import Optional
from models.base_model import ConfiguredBaseModel as BaseModel


class PopulationDataPoint(BaseModel):
    character_count: int
    lfm_count: int


class PopulationPointInTime(BaseModel):
    timestamp: float
    data: Optional[list[dict[str, PopulationDataPoint]]] = None
