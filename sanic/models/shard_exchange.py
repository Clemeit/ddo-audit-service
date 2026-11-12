"""Request models for the Shard Exchange (auction house) endpoints. The field names are the contract with
collection; don't rename them on one side only."""

from uuid import UUID

from constants.server import SERVER_NAMES_LOWERCASE
from constants.shard_exchange import (
    MAX_ITEM_NAME_LENGTH,
    MAX_ITEM_NAMES_PER_REQUEST,
    MAX_LISTINGS_PER_SCAN,
)
from pydantic import BaseModel, Field, field_validator

INT64_MAX = 2**63 - 1
INT32_MAX = 2**31 - 1


class ShardListingApiModel(BaseModel):
    """One auction. Prices are in shards, 0 meaning none; the *_unix times are seconds, 0 meaning unknown."""

    auction_id: int = Field(ge=0, le=INT64_MAX)
    item_id: int = Field(ge=0, le=INT64_MAX)
    # The item's type ID. Every item of one kind has the same one; its name is in item_names.
    did: int = Field(ge=0, le=INT64_MAX)
    quantity: int = Field(
        default=-1, ge=-1, le=INT64_MAX
    )  # -1: the item does not stack
    min_level: int = Field(default=-1, ge=-1, le=INT32_MAX)  # -1: none of its own
    bound: bool = False
    min_bid: int = Field(default=0, ge=0, le=INT64_MAX)
    bid: int = Field(default=0, ge=0, le=INT64_MAX)  # the current bid; 0 = no bid yet
    buyout: int = Field(default=0, ge=0, le=INT64_MAX)
    # Character IDs in the same form as the characters endpoint uses. 0 = none.
    bidder_id: int = Field(default=0, ge=0, le=INT64_MAX)
    poster_id: int = Field(default=0, ge=0, le=INT64_MAX)
    posted_unix: int = Field(default=0, ge=0, le=INT64_MAX)
    expires_unix: int = Field(default=0, ge=0, le=INT64_MAX)
    duration_s: int = Field(default=0, ge=0, le=INT32_MAX)
    # Extra numeric attributes of the item as received (ID -> value); stored as they are.
    item_props: dict[str, int] = Field(default_factory=dict)


class ShardExchangeRequestApiModel(BaseModel):
    """One scan of one server's Shard Exchange: a full snapshot. A listing missing from a complete scan has ended."""

    server: str
    # Identifies the scan; posting the same scan again changes nothing.
    scan_id: UUID
    started_unix: int = Field(gt=0, le=INT64_MAX)
    finished_unix: int = Field(gt=0, le=INT64_MAX)
    # False means the scan stopped early: its listings are recorded but nothing is inferred from what is missing.
    complete: bool
    pages: int = Field(default=0, ge=0, le=INT32_MAX)
    listings: list[ShardListingApiModel] = Field(max_length=MAX_LISTINGS_PER_SCAN)

    @field_validator("server")
    @classmethod
    def _server_is_known(cls, value: str) -> str:
        server = value.strip().lower()
        if server not in SERVER_NAMES_LOWERCASE:
            raise ValueError(f"unknown server: {value}")
        return server


class ItemNameApiModel(BaseModel):
    # The item's type ID (the did that scans carry).
    did: int = Field(ge=0, le=INT64_MAX)
    name: str = Field(min_length=1, max_length=MAX_ITEM_NAME_LENGTH)


class ItemNamesRequestApiModel(BaseModel):
    items: list[ItemNameApiModel] = Field(max_length=MAX_ITEM_NAMES_PER_REQUEST)
