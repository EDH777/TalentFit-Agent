from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UserOut(BaseModel):
    id: int
    username: str
    role: Literal["admin", "hr"]
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
