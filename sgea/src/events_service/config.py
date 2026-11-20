from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = "SGEA - Events Service (Domain)"
    database_url: str = "sqlite+aiosqlite:///./events.db"

    rest_port: int = 8002
    grpc_port: int = 50052

    model_config = SettingsConfigDict(
        env_prefix="EVENTS_",
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
