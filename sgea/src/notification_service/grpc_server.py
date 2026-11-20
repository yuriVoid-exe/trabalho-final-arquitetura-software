import grpc
from sqlalchemy import select

from sgea.src.notification_service.database import async_session_maker
from sgea.src.notification_service.models import (
    Notification,
    NotificationChannel,
    NotificationStatus,
)
from sgea.src.protos import notifications_pb2, notifications_pb2_grpc

PROTO_TO_CHANNEL = {
    notifications_pb2.EMAIL: NotificationChannel.EMAIL,
    notifications_pb2.SYSTEM: NotificationChannel.SYSTEM,
}
CHANNEL_TO_PROTO = {v: k for k, v in PROTO_TO_CHANNEL.items()}

STATUS_TO_PROTO = {
    NotificationStatus.SENT: notifications_pb2.SENT,
    NotificationStatus.FAILED: notifications_pb2.FAILED,
}


def _to_notification_response(
    notif: Notification,
) -> notifications_pb2.NotificationResponse:
    return notifications_pb2.NotificationResponse(
        notification_id=notif.id,
        recipient_id=notif.recipient_id,
        recipient_email=notif.recipient_email,
        recipient_name=notif.recipient_name,
        subject=notif.subject,
        message=notif.message,
        channel=CHANNEL_TO_PROTO.get(
            notif.channel, notifications_pb2.NOTIFICATION_CHANNEL_UNSPECIFIED
        ),
        status=STATUS_TO_PROTO.get(
            notif.status, notifications_pb2.NOTIFICATION_STATUS_UNSPECIFIED
        ),
        source_system=notif.source_system,
        created_at=notif.created_at.isoformat(),
    )


class NotificationGrpcServicer(
    notifications_pb2_grpc.NotificationServiceServicer
):
    async def SendNotification(
        self,
        request: notifications_pb2.SendNotificationRequest,
        context: grpc.aio.ServicerContext,
    ) -> notifications_pb2.NotificationResponse:
        if (
            not request.recipient_id.strip()
            or not request.recipient_email.strip()
            or not request.recipient_name.strip()
            or not request.subject.strip()
            or not request.message.strip()
        ):
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Todos os dados do destinatário, assunto e mensagem são obrigatórios.",
            )

        if request.channel not in PROTO_TO_CHANNEL:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Canal de notificação inválido.",
            )

        channel = PROTO_TO_CHANNEL[request.channel]
        source_system = request.source_system.strip() or "SGEA"

        notification = Notification(
            recipient_id=request.recipient_id.strip(),
            recipient_email=request.recipient_email.strip(),
            recipient_name=request.recipient_name.strip(),
            subject=request.subject.strip(),
            message=request.message.strip(),
            channel=channel,
            status=NotificationStatus.SENT,
            source_system=source_system,
        )

        async with async_session_maker() as db:
            db.add(notification)
            await db.commit()
            await db.refresh(notification)

        print(
            f"[notification_service] [{channel.value}] ({source_system}) "
            f"Para {notification.recipient_email}: {notification.subject}"
        )
        return _to_notification_response(notification)

    async def ListNotificationsByRecipient(
        self,
        request: notifications_pb2.ListNotificationsByRecipientRequest,
        context: grpc.aio.ServicerContext,
    ) -> notifications_pb2.ListNotificationsResponse:
        if not request.recipient_id.strip():
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "recipient_id é obrigatório.",
            )

        async with async_session_maker() as db:
            result = await db.execute(
                select(Notification)
                .where(Notification.recipient_id == request.recipient_id.strip())
                .order_by(Notification.created_at.desc())
            )
            notifications = list(result.scalars().all())

        return notifications_pb2.ListNotificationsResponse(
            notifications=[_to_notification_response(n) for n in notifications]
        )


def create_grpc_server(port: int) -> grpc.aio.Server:
    server = grpc.aio.server()
    notifications_pb2_grpc.add_NotificationServiceServicer_to_server(
        NotificationGrpcServicer(), server
    )
    server.add_insecure_port(f"[::]:{port}")
    return server
