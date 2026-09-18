"""Environment-only settings; never include values in configuration errors."""

from typing import Annotated, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Port = Annotated[int, Field(ge=1, le=65535)]


class ConfigurationError(RuntimeError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TTCP_", hide_input_in_errors=True)

    postgres_host: str = Field(min_length=1)
    postgres_port: Port = 5432
    postgres_db: str = Field(min_length=1)
    postgres_user: str = Field(min_length=1)
    postgres_password: SecretStr
    redis_host: str = Field(min_length=1)
    redis_port: Port = 6379
    redis_password: SecretStr
    dependency_timeout: float = Field(default=2, ge=0.1, le=10, allow_inf_nan=False)
    worker_check_interval: float = Field(default=5, ge=1, le=60, allow_inf_nan=False)
    api_host: str = Field(default="0.0.0.0", min_length=1)
    api_port: Port = 8080
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("postgres_password", "redis_password")
    @classmethod
    def nonempty_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("postgres_host", "postgres_db", "postgres_user", "redis_host", "api_host")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        # Pydantic errors can contain raw input/context: only expose field names.
        fields = sorted({"TTCP_" + str(e["loc"][0]).upper() for e in exc.errors()})
        raise ConfigurationError("Missing or invalid configuration: " + ", ".join(fields)) from None
