from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from apps.execution.ports import ClosedModel


class UserInput(ClosedModel):
    display_name: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    expires_at: datetime | None = None
    device_limit: int = Field(default=3, strict=True, ge=0)
    access_mode: Literal["selected", "all"] = "selected"

    @field_validator("display_name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Name is blank")
        return value

    @field_validator("expires_at")
    @classmethod
    def aware_expiration(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("Expiration requires a timezone")
        return value


class UserUpdate(ClosedModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    expires_at: datetime | None = None
    device_limit: int | None = Field(default=None, strict=True, ge=0)

    @field_validator("display_name")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Name is blank")
        return value

    @field_validator("expires_at")
    @classmethod
    def aware_expiration(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("Expiration requires a timezone")
        return value


class Enabled(ClosedModel):
    enabled: bool


class AccessInput(ClosedModel):
    access_mode: Literal["selected", "all"]
    server_ids: list[UUID] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def valid_selection(self):
        if len(set(self.server_ids)) != len(self.server_ids):
            raise ValueError("Duplicate server")
        if self.access_mode == "all" and self.server_ids:
            raise ValueError("All access cannot have a selection")
        return self


class DeviceInput(ClosedModel):
    name: str = Field(min_length=1, max_length=255)
    platform: str | None = Field(default=None, max_length=64)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Name is blank")
        return value
