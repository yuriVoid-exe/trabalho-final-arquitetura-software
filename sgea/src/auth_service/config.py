from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = "SGEA - Auth Service (SOA)"
    database_url: str = "sqlite+aiosqlite:///./auth.db"

    jwt_secret_key: str = "sgea-dev-secret-key-trocar-em-producao"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    rest_port: int = 8001
    grpc_port: int = 50051

    # Seed inicial para facilitar testes e avaliação RBAC
    default_admin_name: str = "Administrador SGEA"
    default_admin_email: str = "admin@sgea.edu.br"
    default_admin_password: str = "admin123"

    model_config = SettingsConfigDict(
        env_prefix="AUTH_",
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
