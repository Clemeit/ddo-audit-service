"""Limits and rules for the Shard Exchange (auction house) uploads."""

# A scan is a few MB of JSON; this is far above that and only guards against a compression bomb.
MAX_INFLATED_BODY_BYTES = 64 * 1024 * 1024

# A server has a few thousand listings. This is a sanity limit, not an expectation.
MAX_LISTINGS_PER_SCAN = 100_000

# Rows older than this are deleted (the two hypertables also have a Timescale retention policy of the
# same length, see postgres/init.sql). Ended listings and scan records are pruned by the scan handler.
RETENTION_DAYS = 180

# A listing's expiry time is an estimate, good to a few seconds.
# A listing that vanished within this long before its expiry time is counted as expired, not sold.
EXPIRY_SLACK_SECONDS = 120

# A complete scan marks every listing it did not see as ended. If it saw fewer than this fraction of the
# listings we think are active, something is wrong with the scan (for example one that missed part of
# the market), so nothing is marked ended.
MIN_ACTIVE_FRACTION_TO_END_LISTINGS = 0.5

END_REASON_EXPIRED = "expired"
END_REASON_SOLD_OR_CANCELLED = "sold_or_cancelled"
END_REASON_UNKNOWN = "unknown"

# Price history can be asked for as far back as it is kept.
MAX_PRICE_HISTORY_DAYS = RETENTION_DAYS
DEFAULT_PRICE_HISTORY_DAYS = RETENTION_DAYS
PRICE_HISTORY_BUCKETS = ("scan", "day")  # a row per scan (about hourly), or per UTC day

# Item name search and loading.
ITEM_SEARCH_MIN_LENGTH = 2
ITEM_SEARCH_DEFAULT_LIMIT = 20
ITEM_SEARCH_MAX_LIMIT = 100
MAX_ITEM_NAMES_PER_REQUEST = 20_000
MAX_ITEM_NAME_LENGTH = 200
