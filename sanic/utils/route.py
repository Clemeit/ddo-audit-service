import re

from constants.route import OPEN_METHODS, OPEN_ROUTES, VERSION_PATTERN

from sanic.request import Request


def is_method_open(request: Request) -> bool:
    return request.method in OPEN_METHODS


def is_route_open(request: Request) -> bool:
    stripped_path = VERSION_PATTERN.sub("/", request.path)
    for method, pattern in OPEN_ROUTES:
        if request.method == method and re.match(pattern, stripped_path):
            return True
    return False
