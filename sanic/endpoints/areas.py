"""
Area endpoints.
"""

import services.postgres as postgres_client

from sanic import Blueprint
from sanic.response import json
from sanic.request import Request

from models.area import Area

area_blueprint = Blueprint("areas", url_prefix="/areas", version=1)


@area_blueprint.get("/<area_name:str>")
async def get_area_by_name(
    request: Request,
):
    """
    Method: GET

    Route: /areas/<area_name:str>

    Description: Get area by name.
    """

    try:
        area: Area = postgres_client.get_area_by_name()
        if not area:
            return json({"message": "area not found"}, status=404)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": area.model_dump()})


@area_blueprint.get("/<area_id:int>")
async def get_area_by_id(request: Request, area_id: int):
    """
    Method: GET

    Route: /area/<area_id:int>

    Description: Get area by id.
    """

    try:
        area: Area = postgres_client.get_area_by_id(area_id)
        if not area:
            return json({"message": "area not found"}, status=404)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": area.model_dump()})


@area_blueprint.post("")
async def update_areas(request: Request):
    """
    Method: POST

    Route: /areas

    Description: Update areas.
    """

    try:
        raw_areas_list = request.json.get("areas")
        if not raw_areas_list:
            return json({"message": "no areas provided"}, status=400)
        areas_list: list[Area] = [Area(**area) for area in raw_areas_list]
        postgres_client.update_areas(areas_list)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"message": "areas updated"})
