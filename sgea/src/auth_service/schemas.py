from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from sgea.src.auth_service.models import UserRole


class UserRegister(BaseModel):
    name: str = Field(..., min_length=2, max_length=120, examples=["Maria Silva"])
    email: EmailStr = Field(..., examples=["maria@universidade.edu.br"])
    password: str = Field(..., min_length=6, max_length=128, examples=["senha123"])


class AdminUserCreate(UserRegister):
    role: UserRole = Field(default=UserRole.PARTICIPANT)


class UserLogin(BaseModel):
    email: EmailStr = Field(..., examples=["admin@sgea.edu.br"])
    password: str = Field(..., min_length=1, examples=["admin123"])


class UserOut(BaseModel):
    id: str
    name: str
    email: EmailStr
    role: UserRole
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserRoleUpdate(BaseModel):
    role: UserRole


class UserStatusUpdate(BaseModel):
    is_active: bool
