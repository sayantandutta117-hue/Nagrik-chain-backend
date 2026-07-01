from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./nagarik.db"
    api_key: str = "dev-secret"
    ipfs_api_url: str = "http://127.0.0.1:5001"
    fabric_gateway_url: str = "http://127.0.0.1:7051"
    ethereum_rpc_url: str = "http://127.0.0.1:8545"
    sourceafis_url: str | None = None
    deepface_url: str | None = None
    ir_liveness_url: str | None = None
    llm_provider: str = "offline"
    hsm_provider: str = "local"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
