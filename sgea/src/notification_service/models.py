import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, Enum as SAEnum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from sgea.src.notification_service.database import Base


class NotificationChannel(str, Enum):
    EMAIL = "EMAIL"
    SYSTEM = "SYSTEM"


class NotificationStatus(str, Enum):
    SENT = "SENT"
    FAILED = "FAILED"


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    recipient_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_name: Mapped[str] = mapped_column(String(120), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[NotificationChannel] = mapped_column(
        SAEnum(NotificationChannel), default=NotificationChannel.EMAIL, nullable=False
    )
    status: Mapped[NotificationStatus] = mapped_column(
        SAEnum(NotificationStatus), default=NotificationStatus.SENT, nullable=False
    )
    source_system: Mapped[str] = mapped_column(
        String(60), default="SGEA", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
