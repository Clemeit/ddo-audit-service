"""
Area endpoints.
"""

import services.postgres as postgres_client

from sanic import Blueprint
from sanic.response import json
from sanic.request import Request
from utils.areas import get_areas

from models.area import Area

area_blueprint = Blueprint("areas", url_prefix="/areas", version=1)


@area_blueprint.get("")
async def get_all_areas(request: Request):
    """
    Method: GET

    Route: /areas

    Description: Get all areas.
    """

    try:
        force = request.args.get("force", "false").lower() == "true"
        areas_list, source, timestamp = get_areas(skip_cache=force)
        if not areas_list:
            return json({"message": "no areas found"}, status=404)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": areas_list, "source": source, "timestamp": timestamp})


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
        raw_areas_list = request.json
        if not raw_areas_list:
            return json({"message": "no areas provided"}, status=400)
        areas_list: list[Area] = []
        for area in raw_areas_list:
            area: dict
            areas_list.append(
                Area(
                    id=int(area.get("areaid", 0)),
                    name=area.get("name", ""),
                    is_public=True if area.get("ispublicspace") == "1" else False,
                    region=area.get("region", ""),
                    is_wilderness=False,
                )
            )

        postgres_client.update_areas(areas_list)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"message": "areas updated"})
