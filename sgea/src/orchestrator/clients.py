from contextlib import asynccontextmanager

import grpc

from sgea.src.orchestrator.config import settings
from sgea.src.protos import (
    auth_pb2_grpc,
    events_pb2_grpc,
    notifications_pb2_grpc,
    registrations_pb2_grpc,
)


@asynccontextmanager
async def get_auth_stub():
    async with grpc.aio.insecure_channel(settings.auth_grpc_target) as channel:
        yield auth_pb2_grpc.AuthServiceStub(channel)


@asynccontextmanager
async def get_events_stub():
    async with grpc.aio.insecure_channel(settings.events_grpc_target) as channel:
        yield events_pb2_grpc.EventsServiceStub(channel)


@asynccontextmanager
async def get_registrations_stub():
    async with grpc.aio.insecure_channel(
        settings.registrations_grpc_target
    ) as channel:
        yield registrations_pb2_grpc.RegistrationsServiceStub(channel)


@asynccontextmanager
async def get_notification_stub():
    async with grpc.aio.insecure_channel(
        settings.notification_grpc_target
    ) as channel:
        yield notifications_pb2_grpc.NotificationServiceStub(channel)
