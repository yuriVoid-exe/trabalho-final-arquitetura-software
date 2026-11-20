import uuid
from datetime import datetime, timezone

import grpc
from sqlalchemy import select

from sgea.src.protos import registrations_pb2, registrations_pb2_grpc
from sgea.src.registrations_service.database import async_session_maker
from sgea.src.registrations_service.models import Registration, RegistrationStatus

REG_STATUS_TO_PROTO = {
    RegistrationStatus.CONFIRMED: registrations_pb2.CONFIRMED,
    RegistrationStatus.CANCELLED: registrations_pb2.CANCELLED,
}


def _to_registration_response(
    reg: Registration,
) -> registrations_pb2.RegistrationResponse:
    return registrations_pb2.RegistrationResponse(
        registration_id=reg.id,
        event_id=reg.event_id,
        user_id=reg.user_id,
        user_name=reg.user_name,
        user_email=reg.user_email,
        event_title=reg.event_title,
        status=REG_STATUS_TO_PROTO.get(
            reg.status, registrations_pb2.REGISTRATION_STATUS_UNSPECIFIED
        ),
        attendance_recorded=reg.attendance_recorded,
        certificate_code=reg.certificate_code or "",
        created_at=reg.created_at.isoformat(),
    )


class RegistrationsGrpcServicer(
    registrations_pb2_grpc.RegistrationsServiceServicer
):
    async def CreateRegistration(
        self,
        request: registrations_pb2.CreateRegistrationRequest,
        context: grpc.aio.ServicerContext,
    ) -> registrations_pb2.RegistrationResponse:
        if (
            not request.event_id.strip()
            or not request.user_id.strip()
            or not request.user_name.strip()
            or not request.user_email.strip()
            or not request.event_title.strip()
        ):
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Todos os campos da inscrição são obrigatórios.",
            )

        async with async_session_maker() as db:
            existing = await db.execute(
                select(Registration).where(
                    Registration.event_id == request.event_id.strip(),
                    Registration.user_id == request.user_id.strip(),
                    Registration.status == RegistrationStatus.CONFIRMED,
                )
            )
            if existing.scalar_one_or_none():
                await context.abort(
                    grpc.StatusCode.ALREADY_EXISTS,
                    "O participante já possui inscrição confirmada neste evento.",
                )

            registration = Registration(
                event_id=request.event_id.strip(),
                user_id=request.user_id.strip(),
                user_name=request.user_name.strip(),
                user_email=request.user_email.strip(),
                event_title=request.event_title.strip(),
                status=RegistrationStatus.CONFIRMED,
            )
            db.add(registration)
            await db.commit()
            await db.refresh(registration)

        return _to_registration_response(registration)

    async def CancelRegistration(
        self,
        request: registrations_pb2.CancelRegistrationRequest,
        context: grpc.aio.ServicerContext,
    ) -> registrations_pb2.RegistrationResponse:
        if not request.registration_id.strip() or not request.requester_id.strip():
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "registration_id e requester_id são obrigatórios.",
            )

        async with async_session_maker() as db:
            result = await db.execute(
                select(Registration).where(
                    Registration.id == request.registration_id.strip()
                )
            )
            reg = result.scalar_one_or_none()
            if not reg:
                await context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    "Inscrição não encontrada.",
                )

            if (
                not request.requester_is_admin
                and reg.user_id != request.requester_id.strip()
            ):
                await context.abort(
                    grpc.StatusCode.PERMISSION_DENIED,
                    "Apenas o próprio participante ou um administrador pode cancelar esta inscrição.",
                )

            if reg.status == RegistrationStatus.CANCELLED:
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Esta inscrição já se encontra cancelada.",
                )

            reg.status = RegistrationStatus.CANCELLED
            reg.updated_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(reg)

        return _to_registration_response(reg)

    async def GetRegistration(
        self,
        request: registrations_pb2.GetRegistrationRequest,
        context: grpc.aio.ServicerContext,
    ) -> registrations_pb2.RegistrationResponse:
        if not request.registration_id.strip():
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "registration_id é obrigatório.",
            )

        async with async_session_maker() as db:
            result = await db.execute(
                select(Registration).where(
                    Registration.id == request.registration_id.strip()
                )
            )
            reg = result.scalar_one_or_none()

        if not reg:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                "Inscrição não encontrada.",
            )

        return _to_registration_response(reg)

    async def ListEventRegistrations(
        self,
        request: registrations_pb2.ListEventRegistrationsRequest,
        context: grpc.aio.ServicerContext,
    ) -> registrations_pb2.ListRegistrationsResponse:
        if not request.event_id.strip():
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "event_id é obrigatório.",
            )

        stmt = select(Registration).where(
            Registration.event_id == request.event_id.strip()
        )
        if not request.include_cancelled:
            stmt = stmt.where(
                Registration.status == RegistrationStatus.CONFIRMED
            )
        stmt = stmt.order_by(Registration.created_at.asc())

        async with async_session_maker() as db:
            result = await db.execute(stmt)
            regs = list(result.scalars().all())

        return registrations_pb2.ListRegistrationsResponse(
            registrations=[_to_registration_response(r) for r in regs]
        )

    async def ListUserRegistrations(
        self,
        request: registrations_pb2.ListUserRegistrationsRequest,
        context: grpc.aio.ServicerContext,
    ) -> registrations_pb2.ListRegistrationsResponse:
        if not request.user_id.strip():
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "user_id é obrigatório.",
            )

        stmt = select(Registration).where(
            Registration.user_id == request.user_id.strip()
        )
        if not request.include_cancelled:
            stmt = stmt.where(
                Registration.status == RegistrationStatus.CONFIRMED
            )
        stmt = stmt.order_by(Registration.created_at.desc())

        async with async_session_maker() as db:
            result = await db.execute(stmt)
            regs = list(result.scalars().all())

        return registrations_pb2.ListRegistrationsResponse(
            registrations=[_to_registration_response(r) for r in regs]
        )

    async def RecordAttendance(
        self,
        request: registrations_pb2.RecordAttendanceRequest,
        context: grpc.aio.ServicerContext,
    ) -> registrations_pb2.RegistrationResponse:
        if not request.registration_id.strip():
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "registration_id é obrigatório.",
            )

        async with async_session_maker() as db:
            result = await db.execute(
                select(Registration).where(
                    Registration.id == request.registration_id.strip()
                )
            )
            reg = result.scalar_one_or_none()
            if not reg:
                await context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    "Inscrição não encontrada.",
                )

            if reg.status != RegistrationStatus.CONFIRMED:
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Não é possível registrar presença em uma inscrição cancelada.",
                )

            now = datetime.now(timezone.utc)
            reg.attendance_recorded = request.attended
            reg.attendance_recorded_at = now if request.attended else None
            reg.updated_at = now
            await db.commit()
            await db.refresh(reg)

        return _to_registration_response(reg)

    async def IssueCertificate(
        self,
        request: registrations_pb2.IssueCertificateRequest,
        context: grpc.aio.ServicerContext,
    ) -> registrations_pb2.CertificateResponse:
        if not request.registration_id.strip() or not request.requester_id.strip():
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "registration_id e requester_id são obrigatórios.",
            )

        async with async_session_maker() as db:
            result = await db.execute(
                select(Registration).where(
                    Registration.id == request.registration_id.strip()
                )
            )
            reg = result.scalar_one_or_none()
            if not reg:
                await context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    "Inscrição não encontrada.",
                )

            if (
                not request.requester_is_admin
                and reg.user_id != request.requester_id.strip()
            ):
                await context.abort(
                    grpc.StatusCode.PERMISSION_DENIED,
                    "Apenas o próprio participante ou um administrador pode emitir este certificado.",
                )

            if reg.status != RegistrationStatus.CONFIRMED:
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Inscrições canceladas não dão direito a certificado.",
                )

            if not reg.attendance_recorded:
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "A presença do participante ainda não foi registrada neste evento.",
                )

            # Emissão idempotente: se já foi emitido, reutiliza o mesmo código
            if not reg.certificate_code:
                now = datetime.now(timezone.utc)
                reg.certificate_code = f"SGEA-{uuid.uuid4().hex[:8].upper()}"
                reg.certificate_issued_at = now
                reg.updated_at = now
                await db.commit()
                await db.refresh(reg)

        return registrations_pb2.CertificateResponse(
            registration_id=reg.id,
            event_id=reg.event_id,
            event_title=reg.event_title,
            user_id=reg.user_id,
            user_name=reg.user_name,
            certificate_code=reg.certificate_code,
            issued_at=reg.certificate_issued_at.isoformat(),
        )

    async def GetRegistrationsSummary(
        self,
        request: registrations_pb2.GetRegistrationsSummaryRequest,
        context: grpc.aio.ServicerContext,
    ) -> registrations_pb2.RegistrationsSummaryResponse:
        async with async_session_maker() as db:
            result = await db.execute(select(Registration))
            regs = list(result.scalars().all())

        return registrations_pb2.RegistrationsSummaryResponse(
            total_registrations=len(regs),
            confirmed_registrations=sum(
                1 for r in regs if r.status == RegistrationStatus.CONFIRMED
            ),
            cancelled_registrations=sum(
                1 for r in regs if r.status == RegistrationStatus.CANCELLED
            ),
            total_attendances=sum(
                1
                for r in regs
                if r.status == RegistrationStatus.CONFIRMED
                and r.attendance_recorded
            ),
            certificates_issued=sum(
                1 for r in regs if r.certificate_code is not None
            ),
        )


def create_grpc_server(port: int) -> grpc.aio.Server:
    server = grpc.aio.server()
    registrations_pb2_grpc.add_RegistrationsServiceServicer_to_server(
        RegistrationsGrpcServicer(), server
    )
    server.add_insecure_port(f"[::]:{port}")
    return server
