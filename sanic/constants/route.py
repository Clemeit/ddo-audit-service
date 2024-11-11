import re

OPEN_ROUTES = []
OPEN_METHODS = ["GET"]
VERSION_PATTERN = re.compile(r"^/v\d+/")
