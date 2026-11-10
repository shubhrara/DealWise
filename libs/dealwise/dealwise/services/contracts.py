"""Contract repository service. Every method takes the caller's UserContext
(through the service instance) and enforces visibility/ownership rules."""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from dealwise import config
from dealwise.constants import (
    REL_AMENDMENT,
    REL_ORIGINAL,
    RELATION_TYPES,
    ROLE_HISTORICAL,
    ROLES,
    STATUSES,
    VISIBILITIES,
)
from dealwise.db.models import Contract, Vendor, utcnow
from dealwise.security.context import UserContext
from dealwise.security.logging import get_logger
from dealwise.services import lifecycle as lc
from dealwise.services import storage
from dealwise.services.access import audit, can_edit, visible_clause
from dealwise.services.errors import (
    Conflict,
    DuplicateDocument,
    Forbidden,
    NotFound,
    ValidationError,
)
from dealwise.services.vendors import VendorService
from sqlalchemy import func
from sqlmodel import Session, select

log = get_logger("dealwise.contracts")

EDITABLE = {
    "title", "client_name", "category", "department", "status", "contract_value",
    "currency", "signed_date", "start_date", "end_date", "auto_renews",
    "renewal_notice_days", "payment_terms", "notes", "visibility",
    "effective_from", "effective_to",
}  # fmt: skip


@dataclass
class ContractView:
    """A contract plus values computed from its lineage."""

    c: Contract
    vendor_name: Optional[str]
    effective_status: str
    effective_value: Optional[Decimal]
    effective_currency: Optional[str]
    effective_end_date: Optional[date]
    lifecycle: str
    superseded: bool
    can_edit: bool

    @property
    def id(self) -> str:
        return self.c.id


@dataclass
class ContractFilters:
    search: str = ""
    vendor_id: Optional[str] = None
    category: Optional[str] = None
    department: Optional[str] = None
    status: Optional[str] = None  # effective status
    relation_type: Optional[str] = None
    expiring_within_days: Optional[int] = None


def parse_date(v: Any, label: str = "Date") -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v).strip())
    except ValueError:
        raise ValidationError(f"{label} must be in YYYY-MM-DD format.")


def parse_money(v: Any) -> Optional[Decimal]:
    if v is None or str(v).strip() == "":
        return None
    try:
        d = Decimal(str(v).replace(",", "").replace(" ", ""))
    except InvalidOperation:
        raise ValidationError("Contract value must be a number.")
    if not d.is_finite() or d < 0 or d >= Decimal(10) ** 16:
        raise ValidationError("Contract value must be a non-negative number.")
    return d.quantize(Decimal("0.01"))


def _clean(v):
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


class ContractService:
    def __init__(self, engine, user: UserContext, storage_root: Path | None = None):
        self.engine = engine
        self.user = user
        self.root = storage_root or config.storage_root()
        self.vendors = VendorService(engine, user)

    def _session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    # -- validation ------------------------------------------------------
    def _validate(self, c: Contract) -> None:
        if not (c.title or "").strip():
            raise ValidationError("Title is required.")
        if c.role not in ROLES:
            raise ValidationError("Invalid role.")
        if c.status not in STATUSES:
            raise ValidationError("Invalid status.")
        if c.visibility not in VISIBILITIES:
            raise ValidationError("Invalid visibility.")
        if c.relation_type not in RELATION_TYPES:
            raise ValidationError("Invalid relation type.")
        if c.contract_value is not None:
            if not c.currency or not re.fullmatch(r"[A-Z]{3}", c.currency):
                raise ValidationError(
                    "A 3-letter currency code (e.g. USD, EUR, INR) is required with a value."
                )
        elif c.currency and not re.fullmatch(r"[A-Z]{3}", c.currency):
            raise ValidationError("Currency must be a 3-letter code (e.g. USD).")
        if c.start_date and c.end_date and c.end_date < c.start_date:
            raise ValidationError("End date cannot be before the start date.")
        if c.effective_from and c.effective_to and c.effective_to < c.effective_from:
            raise ValidationError("Effective-to cannot be before effective-from.")
        if c.renewal_notice_days is not None and c.renewal_notice_days < 0:
            raise ValidationError("Renewal notice days cannot be negative.")

    # -- fetch -----------------------------------------------------------
    def _get_row(self, session, contract_id: str) -> Contract:
        c = session.get(Contract, contract_id)
        if c is None or not session.exec(
            select(Contract.id).where(Contract.id == contract_id, visible_clause(self.user))
        ).first():
            raise NotFound("Contract not found.")  # missing and hidden look identical
        return c

    def _lineage_rows(self, session, lineage_id: str, role: str) -> list[Contract]:
        return list(
            session.exec(
                select(Contract).where(
                    visible_clause(self.user),
                    Contract.lineage_id == lineage_id,
                    Contract.role == role,
                )
            ).all()
        )

    def _views(self, session, rows: list[Contract], today: date) -> list[ContractView]:
        vnames = {
            v.id: v.name
            for v in session.exec(select(Vendor).where(Vendor.org_id == self.user.org_id)).all()
        }
        by_lineage: dict[str, list[Contract]] = {}
        for c in rows:
            by_lineage.setdefault(c.lineage_id, []).append(c)
        views = []
        for docs in by_lineage.values():
            sup = lc.superseded_ids(docs)
            for c in docs:
                if lc.is_base(c):
                    t = lc.resolve_terms(c, docs, today)
                    ev, ecur, eend = t.value, t.currency, t.end_date
                else:
                    ev, ecur, eend = c.contract_value, c.currency, c.end_date
                views.append(
                    ContractView(
                        c=c,
                        vendor_name=vnames.get(c.vendor_id),
                        effective_status=lc.effective_status(c, today, sup, eend),
                        effective_value=ev,
                        effective_currency=ecur,
                        effective_end_date=eend,
                        lifecycle=lc.lifecycle_label(c),
                        superseded=c.id in sup,
                        can_edit=can_edit(self.user, c),
                    )
                )
        return views

    # -- queries ---------------------------------------------------------
    def list(
        self,
        filters: ContractFilters | None = None,
        *,
        role: str = ROLE_HISTORICAL,
        view: str = "current",
        sort: str = "updated",
        today: date | None = None,
    ) -> list[ContractView]:
        """``role`` is explicit: callers must say whether they want historical
        contracts or proposals; the two are never mixed."""
        if role not in ROLES:
            raise ValidationError("Invalid role.")
        today = today or date.today()
        f = filters or ContractFilters()
        with self._session() as session:
            rows = session.exec(
                select(Contract).where(visible_clause(self.user), Contract.role == role)
            ).all()
            views = self._views(session, list(rows), today)

        if view == "current":
            views = [v for v in views if lc.is_base(v.c) and not v.superseded]
        terms = [t for t in re.split(r"\s+", (f.search or "").strip().lower()) if t]
        out = []
        for v in views:
            c = v.c
            if f.vendor_id and c.vendor_id != f.vendor_id:
                continue
            if f.category and (c.category or "").lower() != f.category.lower():
                continue
            if f.department and (c.department or "").lower() != f.department.lower():
                continue
            if f.status and v.effective_status != f.status:
                continue
            if f.relation_type and c.relation_type != f.relation_type:
                continue
            if f.expiring_within_days is not None:
                e = v.effective_end_date
                if v.effective_status != "active" or e is None or not (
                    0 <= (e - today).days <= f.expiring_within_days
                ):
                    continue
            if terms:
                hay = " ".join(
                    str(x or "")
                    for x in (c.title, v.vendor_name, c.client_name, c.category,
                              c.department, c.file_name, c.notes, c.payment_terms)
                ).lower()
                if not all(t in hay for t in terms):
                    continue
            out.append(v)

        far = date.max
        keys = {
            "updated": (lambda v: v.c.updated_at, True),
            "title": (lambda v: v.c.title.lower(), False),
            "vendor": (lambda v: (v.vendor_name or "~").lower(), False),
            "expiry": (lambda v: v.effective_end_date or far, False),
            "value": (lambda v: v.effective_value if v.effective_value is not None else Decimal(-1), True),
        }
        key, rev = keys.get(sort, keys["updated"])
        return sorted(out, key=key, reverse=rev)

    def get(self, contract_id: str, today: date | None = None) -> ContractView:
        today = today or date.today()
        with self._session() as session:
            c = self._get_row(session, contract_id)
            docs = self._lineage_rows(session, c.lineage_id, c.role)
            for v in self._views(session, docs, today):
                if v.id == contract_id:
                    return v
        raise NotFound("Contract not found.")

    def lineage(self, contract_id: str, today: date | None = None) -> list[ContractView]:
        today = today or date.today()
        with self._session() as session:
            c = self._get_row(session, contract_id)
            docs = self._lineage_rows(session, c.lineage_id, c.role)
            views = self._views(session, docs, today)
        return sorted(views, key=lambda v: (v.c.version_number, v.c.created_at))

    def active_version_on(self, contract_id: str, on: date) -> lc.ActiveVersion:
        with self._session() as session:
            c = self._get_row(session, contract_id)
            docs = self._lineage_rows(session, c.lineage_id, c.role)
        return lc.active_version_on(docs, on)

    def facets(self) -> dict[str, list[str]]:
        with self._session() as session:
            rows = session.exec(
                select(Contract.category, Contract.department).where(
                    visible_clause(self.user), Contract.role == ROLE_HISTORICAL
                )
            ).all()
        return {
            "categories": sorted({r[0] for r in rows if r[0]}, key=str.lower),
            "departments": sorted({r[1] for r in rows if r[1]}, key=str.lower),
        }

    # -- create ----------------------------------------------------------
    def create_contract(
        self,
        *,
        file_path: str,
        original_filename: str,
        title: str,
        vendor_name: str,
        force_new_vendor: bool = False,
        role: str = ROLE_HISTORICAL,
        relation_type: str = REL_ORIGINAL,
        parent_contract_id: Optional[str] = None,
        **fields,
    ) -> ContractView:
        unknown = set(fields) - EDITABLE
        if unknown:
            raise ValidationError(f"Unknown field(s): {', '.join(sorted(unknown))}")
        ext = storage.validate_upload(file_path, original_filename, config.max_upload_bytes())
        sha = storage.sha256_of(file_path)
        stored = None
        try:
            with self._session() as session:
                if session.exec(
                    select(Contract.id).where(
                        Contract.org_id == self.user.org_id, Contract.file_sha256 == sha
                    )
                ).first():
                    raise DuplicateDocument(
                        "A document with identical content is already in the repository."
                    )
                res = self.vendors.resolve(session, vendor_name, force_new=force_new_vendor)
                if res.needs_confirmation:
                    names = ", ".join(v.name for v in res.similar)
                    raise Conflict(
                        f"Similar vendor(s) exist: {names}. Pick the existing vendor, or "
                        "tick 'create as a new vendor' to confirm it is different."
                    )
                cid = Contract(title="x", lineage_id="x", owner_id="x").id  # new id
                lineage_id, version = cid, 1
                if relation_type != REL_ORIGINAL:
                    if not parent_contract_id:
                        raise ValidationError(f"A {relation_type} must be linked to an existing contract.")
                    parent = self._get_row(session, parent_contract_id)
                    if not can_edit(self.user, parent):
                        raise Forbidden("Only the owner of a contract (or an admin) can attach versions to it.")
                    if parent.role != role:
                        raise ValidationError("Cannot link a proposal to a historical contract or vice versa.")
                    lineage_id = parent.lineage_id
                    version = (
                        session.exec(
                            select(func.max(Contract.version_number)).where(
                                Contract.org_id == self.user.org_id, Contract.lineage_id == lineage_id
                            )
                        ).one()
                        or 0
                    ) + 1
                elif parent_contract_id:
                    raise ValidationError("An original agreement cannot have a parent.")

                vals = {k: _clean(v) for k, v in fields.items()}
                for k in ("signed_date", "start_date", "end_date", "effective_from", "effective_to"):
                    if k in vals:
                        vals[k] = parse_date(vals[k], k.replace("_", " ").capitalize())
                if "contract_value" in vals:
                    vals["contract_value"] = parse_money(vals["contract_value"])
                if vals.get("currency"):
                    vals["currency"] = vals["currency"].upper()
                if vals.get("renewal_notice_days") is not None:
                    vals["renewal_notice_days"] = int(vals["renewal_notice_days"])
                vals["auto_renews"] = bool(vals.get("auto_renews") or False)
                vals.setdefault("status", "active")
                vals["status"] = vals["status"] or "active"
                vals["visibility"] = vals.get("visibility") or "organization"

                c = Contract(
                    id=cid, org_id=self.user.org_id, role=role,
                    title=(title or "").strip() or Path(original_filename).stem,
                    vendor_id=res.vendor.id, lineage_id=lineage_id,
                    parent_contract_id=parent_contract_id, relation_type=relation_type,
                    version_number=version, owner_id=self.user.user_id, **vals,
                )
                self._validate(c)
                stored = storage.save_file(file_path, original_filename, self.root, self.user.org_id, sha)
                c.file_name, c.stored_path = stored.file_name, stored.rel_path
                c.file_size, c.file_sha256 = stored.size, stored.sha256
                session.add(c)
                audit(session, self.user, "contract.create", "contract", c.id,
                      {"relation_type": relation_type, "ext": ext})
                session.commit()
                log.info("contract created id=%s", c.id)
        except Exception:
            if stored:
                storage.remove_file(self.root, stored.rel_path)
            raise
        return self.get(c.id)

    # -- update ----------------------------------------------------------
    def update_contract(
        self, contract_id: str, changes: dict, *, vendor_name: str | None = None,
        force_new_vendor: bool = False,
    ) -> ContractView:
        unknown = set(changes) - EDITABLE
        if unknown:
            raise ValidationError(f"Unknown field(s): {', '.join(sorted(unknown))}")
        with self._session() as session:
            c = self._get_row(session, contract_id)
            if not can_edit(self.user, c):
                raise Forbidden("You do not have permission to edit this contract.")
            fields = []
            for k, v in changes.items():
                v = _clean(v)
                if k in ("signed_date", "start_date", "end_date", "effective_from", "effective_to"):
                    v = parse_date(v, k.replace("_", " ").capitalize())
                elif k == "contract_value":
                    v = parse_money(v)
                elif k == "currency" and v:
                    v = v.upper()
                elif k == "renewal_notice_days" and v is not None:
                    v = int(v)
                elif k == "auto_renews":
                    v = bool(v)
                elif k == "title" and v is None:
                    raise ValidationError("Title is required.")
                elif k in ("status", "visibility") and v is None:
                    raise ValidationError(f"{k.capitalize()} is required.")
                if getattr(c, k) != v:
                    setattr(c, k, v)
                    fields.append(k)
            if vendor_name is not None and vendor_name.strip():
                res = self.vendors.resolve(session, vendor_name, force_new=force_new_vendor)
                if res.needs_confirmation:
                    names = ", ".join(v.name for v in res.similar)
                    raise Conflict(f"Similar vendor(s) exist: {names}. Pick the existing vendor or confirm a new one.")
                if res.vendor.id != c.vendor_id:
                    c.vendor_id = res.vendor.id
                    fields.append("vendor")
            self._validate(c)
            if fields:
                c.updated_at = utcnow()
                session.add(c)
                audit(session, self.user, "contract.update", "contract", c.id, {"fields": fields})
                session.commit()
        return self.get(contract_id)

    def set_lineage(self, contract_id: str, relation_type: str, parent_contract_id: Optional[str]) -> ContractView:
        """Re-link a leaf document (one with no versions of its own)."""
        with self._session() as session:
            c = self._get_row(session, contract_id)
            if not can_edit(self.user, c):
                raise Forbidden("You do not have permission to edit this contract.")
            if relation_type not in RELATION_TYPES:
                raise ValidationError("Invalid relation type.")
            if session.exec(select(Contract.id).where(Contract.parent_contract_id == c.id)).first():
                raise Conflict("Other versions are linked to this document; re-link or remove those first.")
            if relation_type == REL_ORIGINAL:
                c.parent_contract_id, c.lineage_id, c.version_number = None, c.id, 1
            else:
                if not parent_contract_id or parent_contract_id == c.id:
                    raise ValidationError(f"Choose the contract this {relation_type} applies to.")
                parent = self._get_row(session, parent_contract_id)
                if not can_edit(self.user, parent):
                    raise Forbidden("Only the owner of a contract (or an admin) can attach versions to it.")
                if parent.role != c.role:
                    raise ValidationError("Cannot mix proposals and historical contracts in one lineage.")
                c.parent_contract_id, c.lineage_id = parent.id, parent.lineage_id
                c.version_number = (
                    session.exec(
                        select(func.max(Contract.version_number)).where(
                            Contract.org_id == c.org_id, Contract.lineage_id == parent.lineage_id,
                            Contract.id != c.id,
                        )
                    ).one()
                    or 0
                ) + 1
            c.relation_type = relation_type
            c.updated_at = utcnow()
            audit(session, self.user, "contract.relink", "contract", c.id, {"relation_type": relation_type})
            session.commit()
        return self.get(contract_id)

    # -- delete / download ----------------------------------------------
    def delete_contract(self, contract_id: str) -> None:
        with self._session() as session:
            c = self._get_row(session, contract_id)
            if not can_edit(self.user, c):
                raise Forbidden("You do not have permission to delete this contract.")
            if session.exec(select(Contract.id).where(Contract.parent_contract_id == c.id)).first():
                raise Conflict("Other versions are linked to this contract; delete or re-link them first.")
            path = c.stored_path
            session.delete(c)
            audit(session, self.user, "contract.delete", "contract", contract_id)
            session.commit()
        storage.remove_file(self.root, path)

    def export_file(self, contract_id: str, dest_dir: str | Path) -> Path:
        """Authorised copy of the original file into ``dest_dir`` for download."""
        with self._session() as session:
            c = self._get_row(session, contract_id)
            if not c.stored_path:
                raise NotFound("No file is stored for this contract.")
            src = storage.resolve_path(self.root, c.stored_path)
            name = c.file_name or "document"
            audit(session, self.user, "contract.download", "contract", c.id)
            session.commit()
        if not src.exists():
            raise NotFound("The stored file is missing.")
        out_dir = Path(tempfile.mkdtemp(dir=dest_dir))
        out = out_dir / storage.sanitize_filename(name)
        shutil.copyfile(src, out)
        return out
