"""
Health check endpoint.
"""

from sanic import Blueprint
from sanic.response import json

health_blueprint = Blueprint("health", url_prefix="/health")


@health_blueprint.get("")
async def health_check(request):
    """
    Method: GET

    Route: /health
 
    Description: Health check endpoint.
    """
    return json({"health": "ok"})
