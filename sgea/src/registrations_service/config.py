from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = "SGEA - Registrations Service (Domain)"
    database_url: str = "sqlite+aiosqlite:///./registrations.db"

    rest_port: int = 8003
    grpc_port: int = 50053

    model_config = SettingsConfigDict(
        env_prefix="REGISTRATIONS_",
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
