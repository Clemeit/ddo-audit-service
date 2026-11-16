"""Limits and rules for the Shard Exchange (auction house) uploads."""

# A scan is a few MB of JSON; this is far above that and only guards against a compression bomb.
MAX_INFLATED_BODY_BYTES = 64 * 1024 * 1024

# A server has a few thousand listings. This is a sanity limit, not an expectation.
MAX_LISTINGS_PER_SCAN = 100_000

# Rows older than this are deleted (the two hypertables also have a Timescale retention policy of the
# same length, see postgres/init.sql). Ended listings and scan records are pruned by the scan handler.
RETENTION_DAYS = 180

# A listing's expiry time is an estimate, good to a few seconds.
# A listing is only known to have vanished BEFORE its expiry if the scan that missed it finished more
# than this long before the expiry time; otherwise it may have vanished at expiry.
EXPIRY_SLACK_SECONDS = 120

# A complete scan marks every listing it did not see as ended. If it saw fewer than this fraction of the
# listings we think are active, something is wrong with the scan (for example one that missed part of
# the market), so nothing is marked ended.
MIN_ACTIVE_FRACTION_TO_END_LISTINGS = 0.5

# Why a listing left the exchange. Once someone bids, the poster can no longer cancel, so a listing with
# a bid always sells (to the highest bidder at expiry, or to a buyer who pays the buyout earlier).
# Scans are about hourly, so we only know the listing vanished somewhere between two scans, and a bid
# placed in that gap is invisible to us. We never guess: when the evidence can't tell, the reason says so.
END_REASON_SOLD_BUYOUT = "sold_buyout"  # bid + buyout, vanished well before expiry: bought out
END_REASON_SOLD_AUCTION = "sold_auction"  # bid, no buyout: went to the highest bidder
END_REASON_SOLD_BUYOUT_OR_AUCTION = "sold_buyout_or_auction"  # bid + buyout, vanished around expiry
END_REASON_CANCELLED = "cancelled"  # no bid, no buyout, vanished well before expiry
END_REASON_CANCELLED_OR_BOUGHT_OUT = "cancelled_or_bought_out"  # no bid, buyout, vanished well before expiry
END_REASON_UNSOLD_OR_UNKNOWN = "unsold_or_unknown"  # no bid seen, vanished around expiry
END_REASON_UNKNOWN = "unknown"  # no expiry time
# Legacy values from before bids were considered; old rows may still carry them.
END_REASON_EXPIRED = "expired"
END_REASON_SOLD_OR_CANCELLED = "sold_or_cancelled"

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

# Browsing the active listings.
LISTINGS_DEFAULT_LIMIT = 20
LISTINGS_MAX_LIMIT = 100
LISTINGS_MAX_PAGE = 10_000
LISTINGS_MAX_QUERY_LENGTH = 100
LISTINGS_MAX_QUERY_WORDS = 8
LISTINGS_MAX_LEVEL = 100
LISTINGS_SORTS = ("name", "time_left", "price", "buyout", "unit_price", "unit_buyout")
LISTINGS_DEFAULT_SORT = "name"
# The data changes about once an hour; a short cache spares the database.
LISTINGS_CACHE_SECONDS = 60

# An item's public key: its name in lower case, runs of other characters as one "-". It is computed by
# the database (item_names.slug); this pattern only checks what a client sends.
ITEM_SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
MAX_ITEM_SLUG_LENGTH = 200


def _slugify(name: str) -> str:
    out = []
    for ch in name.lower().replace("'", ""):
        out.append(ch if ch.isascii() and ch.isalnum() else "-")
    return "-".join(part for part in "".join(out).split("-") if part)


def _category(name: str, children: list | None = None, parent: str = "") -> dict:
    slug = f"{parent}.{_slugify(name)}" if parent else _slugify(name)
    return {
        "slug": slug,
        "name": name,
        "children": [
            _category(child, grandchildren, slug)
            for child, grandchildren in (children or [])
        ],
    }


def _leaves(*names: str) -> list:
    return [(name, None) for name in names]


# The Shard Exchange's category tree as players see it. An item's category (item_names.category) is the
# slug of the most specific category it belongs to, e.g. "weapons.simple.dagger"; filtering by a category
# includes everything under it.
SHARD_EXCHANGE_CATEGORIES: list[dict] = [
    _category(name, children)
    for name, children in [
        ("Ammunition", None),
        ("Armor", _leaves("Cloth", "Docent", "Light", "Medium", "Heavy")),
        ("Clothing", _leaves("Cloaks", "Boots", "Headgear", "Gloves", "Belts")),
        ("Collectables", None),
        ("Components", None),
        (
            "Creature Companions",
            _leaves("Ownership Certificates", "Tricks", "Gear"),
        ),
        ("Gems", None),
        ("Ingredients", None),
        ("Item Augments", None),
        ("Jewelry", _leaves("Goggles", "Necklaces", "Rings", "Wrist", "Trinkets")),
        ("Miscellaneous", None),
        ("Potions", None),
        ("Runearms", None),
        ("Scrolls", None),
        ("Shields", None),
        ("Tomes", None),
        ("Tools", None),
        ("Wands", None),
        (
            "Weapons",
            [
                (
                    "Simple",
                    _leaves(
                        "Club",
                        "Dagger",
                        "Heavy Crossbow",
                        "Light Crossbow",
                        "Light Mace",
                        "Heavy Mace",
                        "Morningstar",
                        "Quarterstaff",
                        "Sickle",
                    ),
                ),
                (
                    "Martial",
                    _leaves(
                        "Battle Axe",
                        "Falchion",
                        "Great Axe",
                        "Great Club",
                        "Great Sword",
                        "Hand Axe",
                        "Heavy Pick",
                        "Kukri",
                        "Light Hammer",
                        "Light Pick",
                        "Longbow",
                        "Long Sword",
                        "Maul",
                        "Rapier",
                        "Scimitar",
                        "Shortbow",
                        "Short Sword",
                        "War Hammer",
                    ),
                ),
                (
                    "Exotic",
                    _leaves(
                        "Bastard Sword",
                        "Dwarven Axe",
                        "Great Crossbow",
                        "Kama",
                        "Khopesh",
                        "Repeating Heavy Crossbow",
                        "Repeating Light Crossbow",
                        "Handwrap",
                    ),
                ),
                (
                    "Throwing",
                    _leaves("Axe", "Dagger", "Hammer", "Dart", "Shuriken"),
                ),
            ],
        ),
    ]
]


def _all_slugs(categories: list[dict]) -> list[str]:
    slugs = []
    for category in categories:
        slugs.append(category["slug"])
        slugs.extend(_all_slugs(category["children"]))
    return slugs


SHARD_EXCHANGE_CATEGORY_SLUGS: frozenset[str] = frozenset(
    _all_slugs(SHARD_EXCHANGE_CATEGORIES)
)
MAX_CATEGORY_SLUG_LENGTH = 100
