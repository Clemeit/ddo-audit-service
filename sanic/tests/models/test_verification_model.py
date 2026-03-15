import pytest
from pydantic import ValidationError

from models.verification import VerificationChallengeApiResponse


def test_verification_challenge_api_response_valid_and_dump():
    response = VerificationChallengeApiResponse(
        challenge_word="phoenix",
        is_online=True,
        is_anonymous=False,
        challenge_word_match=True,
        challenge_passed=True,
        access_token="token-123",
    )

    assert response.model_dump() == {
        "challenge_word": "phoenix",
        "is_online": True,
        "is_anonymous": False,
        "challenge_word_match": True,
        "challenge_passed": True,
        "access_token": "token-123",
    }


def test_verification_challenge_missing_required_fields_and_invalid_types():
    with pytest.raises(ValidationError):
        VerificationChallengeApiResponse(challenge_word="only")

    with pytest.raises(ValidationError):
        VerificationChallengeApiResponse(
            challenge_word="x",
            is_online=[],
            is_anonymous=False,
            challenge_word_match=True,
            challenge_passed=True,
            access_token="token",
        )
