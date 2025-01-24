from typing import Optional

from pydantic import BaseModel


class Quest(BaseModel):
    id: int
    alt_id: Optional[int] = None
    area_id: Optional[int] = None
    name: str
    heroic_normal_cr: Optional[int] = None
    epic_normal_cr: Optional[int] = None
    is_free_to_vip: Optional[bool] = False
    required_adventure_pack: Optional[str] = None
    adventure_area: Optional[str] = None
    quest_journal_area: Optional[str] = None
    group_size: Optional[str] = None
    patron: Optional[str] = None
    xp: Optional[dict] = None
    length: Optional[int] = None
    tip: Optional[str] = None
