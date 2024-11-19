"""
Service endpoints.
"""

import services.postgres as postgres_client

from sanic import Blueprint
from sanic.response import json

service_blueprint = Blueprint("service", url_prefix="/service", version=1)


# ===== Client-facing endpoints =====
@service_blueprint.get("/news")
async def get_news(request):
    """
    Method: GET

    Route: /service/news

    Description: Get all service news.
    """
    try:
        service_news = postgres_client.get_news()
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": [news_item.model_dump() for news_item in service_news]})


@service_blueprint.get("/page_messages")
async def get_page_messages(request):
    """
    Method: GET

    Reoute: /service/page_messages

    Description: Get all page messages for all pages.
    """
    try:
        page_messages = postgres_client.get_page_messages()
        serialized_page_messages = [
            page_message.model_dump() for page_message in page_messages
        ]
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": serialized_page_messages})


@service_blueprint.get("/page_messages/<page_name:str>")
async def get_page_message_by_page(request, page_name: str):
    """
    Method: GET

    Reoute: /service/page_messages/<page_name>

    Description: Get all page messages for a specific page.
    """
    try:
        page_messages = postgres_client.get_page_messages(page_name)
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": [page_message.model_dump() for page_message in page_messages]})


# ===================================
