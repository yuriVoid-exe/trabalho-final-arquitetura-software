from contextlib import asynccontextmanager

from fastapi import FastAPI

from sgea.src.registrations_service.config import settings
from sgea.src.registrations_service.database import Base, engine
from sgea.src.registrations_service.grpc_server import create_grpc_server


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    grpc_server = create_grpc_server(settings.grpc_port)
    await grpc_server.start()
    print(f"[registrations_service] gRPC rodando na porta {settings.grpc_port}")

    try:
        yield
    finally:
        await grpc_server.stop(grace=5)
        await engine.dispose()


app = FastAPI(
    title=settings.service_name,
    version="1.0.0",
    description=(
        "Microsserviço de Domínio de Inscrições, Presença e Certificados. "
        "Operações de negócio expostas exclusivamente via gRPC."
    ),
    lifespan=lifespan,
)


@app.get("/health", tags=["Infraestrutura"])
async def health_check():
    return {"status": "ok", "service": "registrations_service"}
