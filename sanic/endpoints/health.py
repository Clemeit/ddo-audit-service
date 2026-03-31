"""
Health check endpoint.
"""

from sanic import Blueprint
from sanic.response import json
from sanic_ext import openapi

health_blueprint = Blueprint("health", url_prefix="/health")


@health_blueprint.get("")
@openapi.summary("Simple health check")
@openapi.response(200, {"application/json": {"description": "Service is up"}})
async def health_check(request):
    """
    Method: GET

    Route: /health

    Description: Health check endpoint.
    """
    return json({"health": "ok"})
