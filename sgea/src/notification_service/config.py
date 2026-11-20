from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = "SGEA - Notification Service (SOA)"
    database_url: str = "sqlite+aiosqlite:///./notifications.db"

    rest_port: int = 8004
    grpc_port: int = 50054
    auth_grpc_target: str = "localhost:50051"

    model_config = SettingsConfigDict(
        env_prefix="NOTIFICATION_",
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
