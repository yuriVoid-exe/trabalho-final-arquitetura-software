from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = "SGEA - API Orchestrator (Gateway)"
    rest_port: int = 8000

    auth_grpc_target: str = "localhost:50051"
    events_grpc_target: str = "localhost:50052"
    registrations_grpc_target: str = "localhost:50053"
    notification_grpc_target: str = "localhost:50054"

    model_config = SettingsConfigDict(
        env_prefix="ORCHESTRATOR_",
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
