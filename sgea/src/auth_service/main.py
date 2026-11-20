from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sgea.src.auth_service.config import settings
from sgea.src.auth_service.database import Base, async_session_maker, engine, get_db
from sgea.src.auth_service.grpc_server import create_grpc_server
from sgea.src.auth_service.models import User, UserRole
from sgea.src.auth_service.schemas import (
    AdminUserCreate,
    TokenResponse,
    UserLogin,
    UserOut,
    UserRegister,
    UserRoleUpdate,
    UserStatusUpdate,
)
from sgea.src.auth_service.security import (
    create_access_token,
    get_current_user,
    hash_password,
    require_roles,
    verify_password,
)


async def seed_default_admin() -> None:
    async with async_session_maker() as db:
        result = await db.execute(
            select(User).where(User.email == settings.default_admin_email)
        )
        if not result.scalar_one_or_none():
            admin = User(
                name=settings.default_admin_name,
                email=settings.default_admin_email,
                hashed_password=hash_password(settings.default_admin_password),
                role=UserRole.ADMIN,
                is_active=True,
            )
            db.add(admin)
            await db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_default_admin()

    grpc_server = create_grpc_server(settings.grpc_port)
    await grpc_server.start()
    print(f"[auth_service] gRPC rodando na porta {settings.grpc_port}")

    try:
        yield
    finally:
        await grpc_server.stop(grace=5)
        await engine.dispose()


app = FastAPI(
    title=settings.service_name,
    version="1.0.0",
    description=(
        "Serviço corporativo SOA de Autenticação, Autorização (JWT + RBAC) "
        "e Gestão de Usuários da Universidade."
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
    return {"status": "ok", "service": "auth_service"}


@app.post(
    "/auth/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Autenticação"],
    summary="Cadastro público de participante",
)
async def register_participant(
    payload: UserRegister,
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe um usuário cadastrado com este e-mail.",
        )

    user = User(
        name=payload.name,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=UserRole.PARTICIPANT,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@app.post(
    "/auth/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    tags=["Autenticação"],
    summary="Autenticar usuário e gerar token JWT",
)
async def login(
    payload: UserLogin,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário desativado.",
        )

    token = create_access_token(user)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@app.get(
    "/auth/me",
    response_model=UserOut,
    tags=["Autenticação"],
    summary="Retorna dados do usuário autenticado",
)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@app.get(
    "/users",
    response_model=list[UserOut],
    tags=["Gestão de Usuários (Admin)"],
    summary="Listar todos os usuários (apenas Administrador)",
)
async def list_users(
    _: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return list(result.scalars().all())


@app.post(
    "/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Gestão de Usuários (Admin)"],
    summary="Criar usuário com papel específico (apenas Administrador)",
)
async def create_user_by_admin(
    payload: AdminUserCreate,
    _: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe um usuário cadastrado com este e-mail.",
        )

    user = User(
        name=payload.name,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@app.get(
    "/users/{user_id}",
    response_model=UserOut,
    tags=["Gestão de Usuários (Admin)"],
    summary="Buscar usuário por ID (apenas Administrador)",
)
async def get_user_by_id(
    user_id: str,
    _: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado.",
        )
    return user


@app.patch(
    "/users/{user_id}/role",
    response_model=UserOut,
    tags=["Gestão de Usuários (Admin)"],
    summary="Alterar papel RBAC do usuário (apenas Administrador)",
)
async def update_user_role(
    user_id: str,
    payload: UserRoleUpdate,
    _: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado.",
        )

    user.role = payload.role
    await db.commit()
    await db.refresh(user)
    return user


@app.patch(
    "/users/{user_id}/status",
    response_model=UserOut,
    tags=["Gestão de Usuários (Admin)"],
    summary="Ativar ou desativar usuário (apenas Administrador)",
)
async def update_user_status(
    user_id: str,
    payload: UserStatusUpdate,
    current_admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    if user_id == current_admin.id and not payload.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O administrador não pode desativar a própria conta.",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado.",
        )

    user.is_active = payload.is_active
    await db.commit()
    await db.refresh(user)
    return user
