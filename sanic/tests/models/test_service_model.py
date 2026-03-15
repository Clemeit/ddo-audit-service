import pytest
from pydantic import ValidationError

from models.service import FeedbackRequest, LogRequest, News, PageMessage


def test_news_model_defaults_and_required_message():
    news = News(message="Server maintenance")

    assert news.model_dump() == {
        "id": None,
        "date": None,
        "message": "Server maintenance",
    }

    with pytest.raises(ValidationError):
        News()


def test_page_message_defaults_and_optional_none():
    page_message = PageMessage(
        message="Downtime notice",
        affected_pages=None,
        start_date=None,
        end_date=None,
    )

    assert page_message.dismissable is False
    assert page_message.type == "info"
    assert page_message.model_dump()["affected_pages"] is None


def test_page_message_rejects_invalid_affected_pages_type():
    with pytest.raises(ValidationError):
        PageMessage(message="x", affected_pages={"bad": "value"})


def test_feedback_request_optional_fields_accept_none():
    request = FeedbackRequest(
        message="Love the app",
        contact=None,
        metadata=None,
        user_id=None,
        session_id=None,
        commit_hash=None,
    )

    assert request.model_dump() == {
        "message": "Love the app",
        "contact": None,
        "metadata": None,
        "user_id": None,
        "session_id": None,
        "commit_hash": None,
    }


def test_feedback_request_requires_message():
    with pytest.raises(ValidationError):
        FeedbackRequest()


def test_log_request_minimal_defaults_and_model_dump():
    request = LogRequest(message="Something happened", level="info")
    dumped = request.model_dump()

    assert dumped["message"] == "Something happened"
    assert dumped["level"] == "info"
    assert dumped["is_internal"] is False
    assert dumped["metadata"] is None


def test_log_request_optional_fields_accept_none():
    request = LogRequest(
        message="Contextful event",
        level="warn",
        timestamp=None,
        session_id=None,
        user_id=None,
        metadata=None,
        is_internal=None,
    )

    assert request.model_dump()["timestamp"] is None
    assert request.model_dump()["is_internal"] is None


def test_log_request_rejects_invalid_metadata_type():
    with pytest.raises(ValidationError):
        LogRequest(message="x", level="info", metadata=["not", "a", "dict"])
