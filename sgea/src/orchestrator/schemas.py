from enum import Enum

from pydantic import BaseModel, Field


class EventTypeEnum(str, Enum):
    ACADEMIC_WEEK = "ACADEMIC_WEEK"
    LECTURE = "LECTURE"
    MINICOURSE = "MINICOURSE"
    WORKSHOP = "WORKSHOP"


class EventStatusEnum(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class EventCreateRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=160, examples=["Semana da Computação"])
    description: str = Field(default="", examples=["Palestras e minicursos de tecnologia."])
    event_type: EventTypeEnum = Field(..., examples=["ACADEMIC_WEEK"])
    location: str = Field(..., min_length=2, max_length=200, examples=["Auditório Central"])
    start_time: str = Field(..., examples=["2026-12-10T14:00:00Z"])
    end_time: str = Field(..., examples=["2026-12-10T18:00:00Z"])
    capacity: int = Field(..., gt=0, examples=[100])


class EventUpdateRequest(EventCreateRequest):
    pass


class EventReviewRequest(BaseModel):
    approve: bool = Field(..., description="true para aprovar, false para rejeitar")


class EventOut(BaseModel):
    event_id: str
    title: str
    description: str
    event_type: EventTypeEnum
    location: str
    start_time: str
    end_time: str
    capacity: int
    occupied_seats: int
    available_seats: int
    organizer_id: str
    status: EventStatusEnum
    created_at: str


class RegistrationCreateRequest(BaseModel):
    event_id: str = Field(..., min_length=1)


class RegistrationOut(BaseModel):
    registration_id: str
    event_id: str
    user_id: str
    user_name: str
    user_email: str
    event_title: str
    status: str
    attendance_recorded: bool
    certificate_code: str
    created_at: str


class OrchestratedRegistrationOut(BaseModel):
    registration: RegistrationOut
    notification_id: str
    notification_status: str


class AttendanceRequest(BaseModel):
    attended: bool = Field(default=True)


class CertificateOut(BaseModel):
    registration_id: str
    event_id: str
    event_title: str
    user_id: str
    user_name: str
    certificate_code: str
    issued_at: str


class NotificationOut(BaseModel):
    notification_id: str
    recipient_id: str
    recipient_email: str
    subject: str
    message: str
    channel: str
    status: str
    source_system: str
    created_at: str


class SummaryReportOut(BaseModel):
    total_events: int
    pending_events: int
    approved_events: int
    rejected_events: int
    total_capacity: int
    total_occupied_seats: int
    total_registrations: int
    confirmed_registrations: int
    cancelled_registrations: int
    total_attendances: int
    certificates_issued: int
