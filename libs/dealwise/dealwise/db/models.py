"""DealWise tables. All use the ``dw_`` prefix; no upstream table is touched.

Layers kept separate (see DEALWISE_AUDIT.md section 4):
  documents (file on disk + hash) -> contract info (this module) ->
  outcomes (Phase 4) -> AI analysis (Phases 3-5).
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from dealwise.constants import DEFAULT_ORG_ID, ROLE_HISTORICAL
from sqlalchemy import JSON, CheckConstraint, Column, Index, Numeric, UniqueConstraint
from sqlmodel import Field, SQLModel


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Vendor(SQLModel, table=True):
    __tablename__ = "dw_vendor"
    __table_args__ = (
        UniqueConstraint("org_id", "normalized_name", name="uq_dw_vendor_org_norm"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    org_id: str = Field(default=DEFAULT_ORG_ID, index=True)
    name: str
    normalized_name: str = Field(index=True)
    website: Optional[str] = None
    notes: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class VendorAlias(SQLModel, table=True):
    __tablename__ = "dw_vendor_alias"
    __table_args__ = (
        UniqueConstraint("org_id", "normalized_alias", name="uq_dw_alias_org_norm"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    org_id: str = Field(default=DEFAULT_ORG_ID, index=True)
    vendor_id: str = Field(foreign_key="dw_vendor.id", index=True)
    alias: str
    normalized_alias: str


class Contract(SQLModel, table=True):
    """One stored document. Amendments/renewals/revisions are separate rows
    that share a ``lineage_id`` with the original agreement."""

    __tablename__ = "dw_contract"
    __table_args__ = (
        CheckConstraint("role IN ('historical','proposal')", name="ck_dw_role"),
        CheckConstraint(
            "status IN ('draft','active','expired','terminated')", name="ck_dw_status"
        ),
        CheckConstraint(
            "relation_type IN ('original','amendment','renewal','revision')",
            name="ck_dw_relation",
        ),
        CheckConstraint(
            "visibility IN ('organization','private')", name="ck_dw_visibility"
        ),
        UniqueConstraint("org_id", "file_sha256", name="uq_dw_contract_org_sha"),
        Index("ix_dw_contract_org_role", "org_id", "role"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    org_id: str = Field(default=DEFAULT_ORG_ID, index=True)
    role: str = Field(default=ROLE_HISTORICAL)

    title: str
    vendor_id: Optional[str] = Field(default=None, foreign_key="dw_vendor.id", index=True)
    client_name: Optional[str] = None
    category: Optional[str] = Field(default=None, index=True)
    department: Optional[str] = Field(default=None, index=True)
    status: str = Field(default="active")

    contract_value: Optional[Decimal] = Field(
        default=None, sa_column=Column(Numeric(18, 2), nullable=True)
    )
    currency: Optional[str] = None
    signed_date: Optional[date] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    auto_renews: bool = False
    renewal_notice_days: Optional[int] = None
    payment_terms: Optional[str] = None
    notes: Optional[str] = None

    # lifecycle / versioning
    lineage_id: str = Field(index=True)
    parent_contract_id: Optional[str] = Field(default=None, index=True)
    relation_type: str = Field(default="original")
    version_number: int = 1
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None

    # access control
    owner_id: str = Field(index=True)
    visibility: str = Field(default="organization")

    # original document
    file_name: Optional[str] = None
    stored_path: Optional[str] = None  # relative to the DealWise storage root
    file_size: Optional[int] = None
    file_sha256: Optional[str] = None
    # link to the upstream Kotaemon index entry (set in Phase 2)
    index_file_id: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AuditLog(SQLModel, table=True):
    """Who did what. ``detail`` holds ids and field *names* only, never contract
    text or values."""

    __tablename__ = "dw_audit_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: str = Field(default=DEFAULT_ORG_ID, index=True)
    user_id: Optional[str] = None
    action: str
    entity_type: str
    entity_id: Optional[str] = None
    detail: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


DW_MODELS = (Vendor, VendorAlias, Contract, AuditLog)
