import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, String
from sqlalchemy.orm import Mapped, mapped_column

from sgea.src.registrations_service.database import Base


class RegistrationStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class Registration(Base):
    __tablename__ = "registrations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    event_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    user_name: Mapped[str] = mapped_column(String(120), nullable=False)
    user_email: Mapped[str] = mapped_column(String(255), nullable=False)
    event_title: Mapped[str] = mapped_column(String(160), nullable=False)

    status: Mapped[RegistrationStatus] = mapped_column(
        SAEnum(RegistrationStatus),
        default=RegistrationStatus.CONFIRMED,
        nullable=False,
    )
    attendance_recorded: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    attendance_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    certificate_code: Mapped[str | None] = mapped_column(
        String(32), unique=True, nullable=True
    )
    certificate_issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
