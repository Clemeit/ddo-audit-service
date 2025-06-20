from pydantic import BaseModel


class VerificationChallengeApiResponse(BaseModel):
    challenge_word: str
    is_online: bool
    is_anonymous: bool
    challenge_word_match: bool
    challenge_passed: bool
    access_token: str
