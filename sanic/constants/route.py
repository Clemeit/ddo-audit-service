import re

OPEN_ROUTES = [
    ("POST", "/service/feedback"),
    ("POST", "/service/log"),
    ("POST", "/user/settings"),
    ("GET", "/user/settings/.*"),  # Allow one-time settings retrieval
    ("POST", "/auth/register"),
    ("POST", "/auth/login"),
]

# Routes that use JWT authentication instead of API key
JWT_PROTECTED_ROUTES = [
    r"^/v?\d*/user/profile",
    r"^/v?\d*/user/settings/persistent",
]

OPEN_METHODS = ["GET"]
VERSION_PATTERN = re.compile(r"^/v\d+/")
