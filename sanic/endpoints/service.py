"""
Service endpoints.
"""

import services.postgres as postgres_client
import services.redis as redis_client

from sanic import Blueprint
from sanic.response import json, empty
from models.service import News, PageMessage, FeedbackRequest, LogRequest
from sanic.request import Request

import uuid

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


@service_blueprint.post("/feedback")
async def post_feedback(request):
    try:
        feedback = FeedbackRequest.model_validate(request.json)
    except Exception:
        return json({"message": "improperly formatted body"}, status=400)

    try:

        # Validate input lengths
        if len(feedback.message) > 5000:
            return json({"message": "feedback message too long"}, status=400)

        if feedback.contact and len(feedback.contact) > 255:
            return json({"message": "contact information too long"}, status=400)

        ticket = uuid.uuid4().hex
        postgres_client.post_feedback(feedback, ticket)
        return json({"data": {"ticket": ticket}})
    except Exception as e:
        return json({"message": str(e)}, status=500)


@service_blueprint.post("/log")
async def post_log(request):
    try:
        log = LogRequest.model_validate(request.json)
    except Exception:
        return json({"message": "improperly formatted body"}, status=400)

    try:
        postgres_client.persist_log(log)
        return empty()
    except Exception as e:
        return json({"message": str(e)}, status=500)


# ===================================


# ======= Internal endpoints ========
@service_blueprint.post("/news")
async def post_news(request: Request):
    """
    Method: POST

    Route: /service/news

    Description: Add news.
    """
    try:
        news_data = News.model_validate(request.json)
        added_news = postgres_client.add_news(news_data)
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": added_news.model_dump()})


@service_blueprint.post("/page_messages")
async def post_page_message(request: Request):
    """
    Method: POST

    Route: /service/page_messages

    Description: Add a page message.
    """
    try:
        page_message_data = PageMessage.model_validate(request.json)
        added_page_message = postgres_client.add_page_message(page_message_data)
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": added_page_message.model_dump()})


# ===================================
