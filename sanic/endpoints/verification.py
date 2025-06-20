"""
Verification endpoints.
"""

import services.redis as redis_client
import services.postgres as postgres_client
import uuid

from sanic import Blueprint
from sanic.response import json

from models.verification import VerificationChallengeApiResponse
from business.verification import get_challenge_word_for_character_by_character_id

verification_blueprint = Blueprint(
    "verification", url_prefix="/verification", version=1
)


@verification_blueprint.get("/<character_id:int>")
async def get_verification_challenge(request, character_id: int):
    """
    Method: GET

    Route: /verification/<character_id:int>

    Description: Get the verification challenge from the Redis cache. Validate if the challenge has passed.
    """
    try:
        is_online = False
        is_anonymous = True
        challenge_word_match = False
        challenge_passed = False
        access_token = ""

        challenge_word = get_challenge_word_for_character_by_character_id(character_id)
        character = redis_client.get_character_by_id(character_id)
        if character:
            is_online = character.is_online
            is_anonymous = character.is_anonymous
            challenge_word_match = character.public_comment == challenge_word

            if is_online and not is_anonymous and challenge_word_match:
                challenge_passed = True
                # check if access token exists in the database, creating
                # and saving a new one if it doesn't
                access_token = postgres_client.get_access_token_by_character_id(
                    character_id
                )
                if not access_token:
                    access_token = uuid.uuid4().hex
                    postgres_client.save_access_token(character_id, access_token)
    except Exception as e:
        return json({"message": str(e)}, status=500)

    response = VerificationChallengeApiResponse(
        challenge_word=challenge_word,
        is_online=is_online,
        is_anonymous=is_anonymous,
        challenge_word_match=challenge_word_match,
        challenge_passed=challenge_passed,
        access_token=access_token,
    )
    return json({"data": response.model_dump()})
