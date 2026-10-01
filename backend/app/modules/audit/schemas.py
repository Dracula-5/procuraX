import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    seq: int
    actor_user_id: uuid.UUID | None
    actor_email: str | None
    actor_type: str
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    summary: str
    changes: dict[str, Any] | None
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="meta")
    request_id: str | None
    created_at: datetime


class AuditPage(BaseModel):
    items: list[AuditEntryOut]
    total: int
