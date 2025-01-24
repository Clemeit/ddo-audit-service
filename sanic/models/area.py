from typing import Optional

from pydantic import BaseModel


class Area(BaseModel):
    id: int
    name: str
    is_public: Optional[bool] = True
    is_wilderness: Optional[bool] = False
    region: Optional[str] = None
