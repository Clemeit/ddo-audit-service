import re

OPEN_ROUTES = [("POST", "/service/feedback"), ("POST", "/service/log")]
OPEN_METHODS = ["GET"]
VERSION_PATTERN = re.compile(r"^/v\d+/")
