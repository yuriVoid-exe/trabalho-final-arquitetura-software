import grpc
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sgea.src.auth_service import database, grpc_server, main
from sgea.src.auth_service.database import Base
from sgea.src.auth_service.main import app, seed_default_admin
from sgea.src.protos import auth_pb2, auth_pb2_grpc


@pytest.fixture
async def isolated_auth_db(tmp_path, monkeypatch):
    """Cria um banco SQLite isolado por teste e redireciona REST e gRPC para ele."""
    test_db_path = tmp_path / "test_auth.db"
    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{test_db_path}", echo=False
    )
    test_session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    monkeypatch.setattr(database, "async_session_maker", test_session_maker)
    monkeypatch.setattr(main, "async_session_maker", test_session_maker)
    monkeypatch.setattr(grpc_server, "async_session_maker", test_session_maker)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_default_admin()

    yield test_session_maker

    await test_engine.dispose()


@pytest.fixture
async def client(isolated_auth_db):
    """Cliente HTTP assíncrono ligado diretamente ao app FastAPI."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def grpc_stub(isolated_auth_db):
    """Sobe um servidor gRPC real em porta efêmera e retorna o stub conectado."""
    server = grpc.aio.server()
    auth_pb2_grpc.add_AuthServiceServicer_to_server(
        grpc_server.AuthGrpcServicer(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()

    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield auth_pb2_grpc.AuthServiceStub(channel)

    await server.stop(grace=0)


async def _login(client: httpx.AsyncClient, email: str, password: str) -> dict:
    resp = await client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200
    return resp.json()


# ==============================================================================
# TESTES REST / OPENAPI / RBAC
# ==============================================================================


async def test_health_check(client: httpx.AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "auth_service"}


async def test_register_participant_and_duplicate_conflict(client: httpx.AsyncClient):
    payload = {
        "name": "Ana Participante",
        "email": "ana@universidade.edu.br",
        "password": "segredo123",
    }
    resp = await client.post("/auth/register", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == payload["email"]
    assert data["role"] == "PARTICIPANT"
    assert data["is_active"] is True

    # E-mail duplicado deve retornar 409 Conflict
    dup_resp = await client.post("/auth/register", json=payload)
    assert dup_resp.status_code == 409


async def test_register_invalid_payload_returns_400(client: httpx.AsyncClient):
    resp = await client.post(
        "/auth/register",
        json={"name": "A", "email": "email-invalido", "password": "123"},
    )
    assert resp.status_code == 400


async def test_login_success_and_wrong_password_returns_401(client: httpx.AsyncClient):
    login_data = await _login(client, "admin@sgea.edu.br", "admin123")
    assert "access_token" in login_data
    assert login_data["user"]["role"] == "ADMIN"

    bad_resp = await client.post(
        "/auth/login",
        json={"email": "admin@sgea.edu.br", "password": "senha-errada"},
    )
    assert bad_resp.status_code == 401


async def test_protected_routes_without_token_return_401(client: httpx.AsyncClient):
    me_resp = await client.get("/auth/me")
    assert me_resp.status_code == 401

    users_resp = await client.get("/users")
    assert users_resp.status_code == 401


async def test_rbac_participant_cannot_access_admin_routes_returns_403(
    client: httpx.AsyncClient,
):
    await client.post(
        "/auth/register",
        json={
            "name": "Carlos Aluno",
            "email": "carlos@universidade.edu.br",
            "password": "segredo123",
        },
    )
    token = (await _login(client, "carlos@universidade.edu.br", "segredo123"))[
        "access_token"
    ]
    headers = {"Authorization": f"Bearer {token}"}

    # Participante pode consultar seu próprio perfil
    me_resp = await client.get("/auth/me", headers=headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["role"] == "PARTICIPANT"

    # Participante não pode listar nem criar usuários administrativos -> 403 Forbidden
    list_resp = await client.get("/users", headers=headers)
    assert list_resp.status_code == 403

    create_resp = await client.post(
        "/users",
        headers=headers,
        json={
            "name": "Outro",
            "email": "outro@universidade.edu.br",
            "password": "segredo123",
            "role": "ORGANIZER",
        },
    )
    assert create_resp.status_code == 403


async def test_admin_user_management_lifecycle(client: httpx.AsyncClient):
    admin_data = await _login(client, "admin@sgea.edu.br", "admin123")
    admin_id = admin_data["user"]["id"]
    headers = {"Authorization": f"Bearer {admin_data['access_token']}"}

    # Criar organizador
    create_resp = await client.post(
        "/users",
        headers=headers,
        json={
            "name": "Prof. Marcos",
            "email": "marcos@universidade.edu.br",
            "password": "segredo123",
            "role": "ORGANIZER",
        },
    )
    assert create_resp.status_code == 201
    org_id = create_resp.json()["id"]

    # Listar usuários
    list_resp = await client.get("/users", headers=headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 2

    # Buscar por ID existente e inexistente (404)
    get_resp = await client.get(f"/users/{org_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["email"] == "marcos@universidade.edu.br"

    not_found_resp = await client.get("/users/id-inexistente", headers=headers)
    assert not_found_resp.status_code == 404

    # Alterar papel RBAC
    role_resp = await client.patch(
        f"/users/{org_id}/role",
        headers=headers,
        json={"role": "ADMIN"},
    )
    assert role_resp.status_code == 200
    assert role_resp.json()["role"] == "ADMIN"

    # Desativar usuário bloqueia login
    status_resp = await client.patch(
        f"/users/{org_id}/status",
        headers=headers,
        json={"is_active": False},
    )
    assert status_resp.status_code == 200
    assert status_resp.json()["is_active"] is False

    blocked_login = await client.post(
        "/auth/login",
        json={"email": "marcos@universidade.edu.br", "password": "segredo123"},
    )
    assert blocked_login.status_code == 401

    # Admin não pode desativar a própria conta -> 400 Bad Request
    self_disable = await client.patch(
        f"/users/{admin_id}/status",
        headers=headers,
        json={"is_active": False},
    )
    assert self_disable.status_code == 400


# ==============================================================================
# TESTES gRPC (AuthService)
# ==============================================================================


async def test_grpc_validate_token_success(
    client: httpx.AsyncClient, grpc_stub: auth_pb2_grpc.AuthServiceStub
):
    await client.post(
        "/auth/register",
        json={
            "name": "Julia Participante",
            "email": "julia@universidade.edu.br",
            "password": "segredo123",
        },
    )
    token = (await _login(client, "julia@universidade.edu.br", "segredo123"))[
        "access_token"
    ]

    grpc_resp = await grpc_stub.ValidateToken(
        auth_pb2.ValidateTokenRequest(token=token)
    )
    assert grpc_resp.valid is True
    assert grpc_resp.email == "julia@universidade.edu.br"
    assert grpc_resp.name == "Julia Participante"
    assert grpc_resp.role == auth_pb2.PARTICIPANT


async def test_grpc_validate_token_unauthenticated(
    grpc_stub: auth_pb2_grpc.AuthServiceStub,
):
    # Token vazio
    with pytest.raises(grpc.aio.AioRpcError) as exc_empty:
        await grpc_stub.ValidateToken(auth_pb2.ValidateTokenRequest(token=""))
    assert exc_empty.value.code() == grpc.StatusCode.UNAUTHENTICATED

    # Token inválido/adulterado
    with pytest.raises(grpc.aio.AioRpcError) as exc_invalid:
        await grpc_stub.ValidateToken(
            auth_pb2.ValidateTokenRequest(token="jwt.invalido.aqui")
        )
    assert exc_invalid.value.code() == grpc.StatusCode.UNAUTHENTICATED


async def test_grpc_get_user_by_id_success_and_errors(
    client: httpx.AsyncClient, grpc_stub: auth_pb2_grpc.AuthServiceStub
):
    admin_data = await _login(client, "admin@sgea.edu.br", "admin123")
    admin_id = admin_data["user"]["id"]

    # Sucesso
    user_resp = await grpc_stub.GetUserById(
        auth_pb2.GetUserByIdRequest(user_id=admin_id)
    )
    assert user_resp.user_id == admin_id
    assert user_resp.email == "admin@sgea.edu.br"
    assert user_resp.role == auth_pb2.ADMIN
    assert user_resp.is_active is True

    # Não encontrado -> NOT_FOUND
    with pytest.raises(grpc.aio.AioRpcError) as exc_not_found:
        await grpc_stub.GetUserById(
            auth_pb2.GetUserByIdRequest(user_id="nao-existe")
        )
    assert exc_not_found.value.code() == grpc.StatusCode.NOT_FOUND

    # Argumento vazio -> INVALID_ARGUMENT
    with pytest.raises(grpc.aio.AioRpcError) as exc_invalid_arg:
        await grpc_stub.GetUserById(auth_pb2.GetUserByIdRequest(user_id=""))
    assert exc_invalid_arg.value.code() == grpc.StatusCode.INVALID_ARGUMENT
