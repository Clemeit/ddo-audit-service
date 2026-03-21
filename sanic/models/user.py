"""
User models for authentication and user management.
"""

from pydantic import BaseModel, Field, field_validator
import re


class UserRegister(BaseModel):
    """Request model for user registration."""

    username: str = Field(..., min_length=5, max_length=25)
    password: str = Field(..., min_length=5, max_length=25)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        """Username must be alphanumeric only."""
        if not re.match(r"^[a-zA-Z0-9]+$", v):
            raise ValueError("Username must be alphanumeric only")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Password must be alphanumeric and common symbols."""
        if not re.match(r'^[a-zA-Z0-9!@#$%^&*()_+\-=\[\]{};:\'",.<>?/`~|\\]+$', v):
            raise ValueError("Password contains invalid characters")
        return v

    @field_validator("password")
    @classmethod
    def password_not_equal_username(cls, v: str, info) -> str:
        """Password cannot be the same as username."""
        if "username" in info.data and v == info.data["username"]:
            raise ValueError("Password cannot be the same as username")
        return v


class UserLogin(BaseModel):
    """Request model for user login."""

    username: str
    password: str


class RefreshTokenRequest(BaseModel):
    """Request model for refresh token rotation."""

    refresh_token: str = Field(..., min_length=1)


class UserProfile(BaseModel):
    """Response model for user profile."""

    id: int
    username: str
    created_at: str


class ChangePassword(BaseModel):
    """Request model for changing password."""

    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=5, max_length=25)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        """Password must be alphanumeric and common symbols."""
        if not re.match(r'^[a-zA-Z0-9!@#$%^&*()_+\-=\[\]{};:\'",.<>?/`~|\\]+$', v):
            raise ValueError("Password contains invalid characters")
        return v


class UserSettings(BaseModel):
    """Request/Response model for user settings."""

    settings: dict = Field(default_factory=dict)


class UserAuthResponse(BaseModel):
    """Response model for authentication endpoints."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = 900
    refresh_expires_in: int = 2592000
    user: UserProfile


class RefreshTokenResponse(BaseModel):
    """Response model for refresh token rotation."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = 900
    refresh_expires_in: int = 2592000


class ChangePasswordResponse(RefreshTokenResponse):
    """Response model for password rotation."""

    message: str
