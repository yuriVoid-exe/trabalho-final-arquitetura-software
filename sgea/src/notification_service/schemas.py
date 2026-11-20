from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from sgea.src.notification_service.models import NotificationChannel, NotificationStatus


class NotificationCreate(BaseModel):
    recipient_id: str = Field(..., min_length=1, examples=["user-123"])
    recipient_email: EmailStr = Field(..., examples=["aluno@universidade.edu.br"])
    recipient_name: str = Field(..., min_length=1, max_length=120, examples=["Maria Silva"])
    subject: str = Field(..., min_length=2, max_length=200, examples=["Inscrição Confirmada"])
    message: str = Field(..., min_length=2, examples=["Sua inscrição foi confirmada com sucesso."])
    channel: NotificationChannel = Field(default=NotificationChannel.EMAIL)
    source_system: str = Field(default="SGEA", min_length=1, max_length=60)


class NotificationOut(BaseModel):
    id: str
    recipient_id: str
    recipient_email: EmailStr
    recipient_name: str
    subject: str
    message: str
    channel: NotificationChannel
    status: NotificationStatus
    source_system: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
