from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sgea.src.notification_service.config import settings
from sgea.src.notification_service.database import Base, engine, get_db
from sgea.src.notification_service.grpc_server import create_grpc_server
from sgea.src.notification_service.models import Notification, NotificationStatus
from sgea.src.notification_service.schemas import NotificationCreate, NotificationOut
from sgea.src.notification_service.security import (
    AuthenticatedUser,
    get_current_user,
    require_roles,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    grpc_server = create_grpc_server(settings.grpc_port)
    await grpc_server.start()
    print(f"[notification_service] gRPC rodando na porta {settings.grpc_port}")

    try:
        yield
    finally:
        await grpc_server.stop(grace=5)
        await engine.dispose()


app = FastAPI(
    title=settings.service_name,
    version="1.0.0",
    description=(
        "Serviço corporativo SOA de Notificações. Expõe contrato OpenAPI "
        "reutilizável por sistemas da universidade e servidor gRPC interno."
    ),
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Dados de entrada inválidos.", "errors": exc.errors()},
    )


@app.get("/health", tags=["Infraestrutura"])
async def health_check():
    return {"status": "ok", "service": "notification_service"}


@app.post(
    "/notifications",
    response_model=NotificationOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Notificações (SOA)"],
    summary="Disparar notificação corporativa (Admin ou Organizador)",
)
async def send_notification(
    payload: NotificationCreate,
    _: AuthenticatedUser = Depends(require_roles("ADMIN", "ORGANIZER")),
    db: AsyncSession = Depends(get_db),
):
    notification = Notification(
        recipient_id=payload.recipient_id,
        recipient_email=str(payload.recipient_email),
        recipient_name=payload.recipient_name,
        subject=payload.subject,
        message=payload.message,
        channel=payload.channel,
        status=NotificationStatus.SENT,
        source_system=payload.source_system,
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)
    return notification


@app.get(
    "/notifications",
    response_model=list[NotificationOut],
    tags=["Notificações (SOA)"],
    summary="Listar todas as notificações disparadas (apenas Administrador)",
)
async def list_all_notifications(
    _: AuthenticatedUser = Depends(require_roles("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Notification).order_by(Notification.created_at.desc())
    )
    return list(result.scalars().all())


@app.get(
    "/notifications/recipient/{recipient_id}",
    response_model=list[NotificationOut],
    tags=["Notificações (SOA)"],
    summary="Listar notificações de um destinatário",
)
async def list_recipient_notifications(
    recipient_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if (
        current_user.role not in ("ADMIN", "ORGANIZER")
        and current_user.user_id != recipient_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você só pode consultar suas próprias notificações.",
        )

    result = await db.execute(
        select(Notification)
        .where(Notification.recipient_id == recipient_id)
        .order_by(Notification.created_at.desc())
    )
    return list(result.scalars().all())


@app.get(
    "/notifications/{notification_id}",
    response_model=NotificationOut,
    tags=["Notificações (SOA)"],
    summary="Buscar detalhes de uma notificação por ID",
)
async def get_notification_by_id(
    notification_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notificação não encontrada.",
        )

    if (
        current_user.role != "ADMIN"
        and notification.recipient_id != current_user.user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permissão insuficiente para visualizar esta notificação.",
        )

    return notification
