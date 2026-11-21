import re

OPEN_ROUTES = [
    ("POST", "/service/feedback"),
    ("POST", "/service/log"),
    ("POST", "/user/settings"),
    ("GET", "/user/settings/[^/]+"),
    ("POST", "/auth/register"),
    ("POST", "/auth/login"),
    ("POST", "/auth/refresh"),
    # Authenticates itself from the refresh cookie (or a Bearer token), so a user whose
    # access token has expired can still end their session.
    ("POST", "/auth/logout$"),
    ("POST", "/shard-exchange/my-bids$"),
]

# Routes that use JWT authentication instead of API key
JWT_PROTECTED_ROUTES = [
    r"^/v?\d*/user/profile(?:/password)?$",
    r"^/v?\d*/user/settings/persistent",
    r"^/v?\d*/auth/account$",
    r"^/v?\d*/user/alerts(?:/.*)?$",
    r"^/v?\d*/user/push-subscriptions/?$",
]

OPEN_METHODS = ["GET"]
VERSION_PATTERN = re.compile(r"^/v\d+/")
