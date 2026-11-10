"""Central vendor management: one record per vendor, duplicate prevention,
aliases and (admin) merge."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from dealwise.constants import ROLE_HISTORICAL, SIMILAR_VENDOR_THRESHOLD
from dealwise.db.models import Contract, Vendor, VendorAlias, utcnow
from dealwise.security.context import UserContext
from dealwise.services.access import audit, visible_clause
from dealwise.services.errors import Conflict, Forbidden, NotFound, ValidationError
from sqlmodel import Session, select

_LEGAL_SUFFIXES = {
    "inc", "incorporated", "ltd", "limited", "llc", "llp", "lp", "plc", "corp",
    "corporation", "co", "company", "gmbh", "ag", "sa", "srl", "bv", "nv", "pvt",
    "pty", "oy", "ab", "kk",
}  # fmt: skip


def normalize_name(name: str) -> str:
    """Lower-case, strip accents/punctuation, drop legal suffixes (Inc, Ltd...)."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[.'’]", "", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    tokens = s.split()
    while len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    if len(tokens) > 1 and tokens[0] == "the":
        tokens.pop(0)
    return " ".join(tokens)


@dataclass
class VendorResolution:
    vendor: Optional[Vendor] = None
    created: bool = False
    needs_confirmation: bool = False
    similar: list[Vendor] = field(default_factory=list)


@dataclass
class VendorSummary:
    id: str
    name: str
    aliases: list[str]
    contract_count: int
    website: Optional[str]
    notes: Optional[str]
    created_at: object


class VendorService:
    def __init__(self, engine, user: UserContext):
        self.engine = engine
        self.user = user

    def _session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    # -- visibility ------------------------------------------------------
    def _visible_vendor_ids(self, session) -> set[str]:
        u = self.user
        if u.is_admin:
            return set(session.exec(select(Vendor.id).where(Vendor.org_id == u.org_id)).all())
        via_contracts = session.exec(
            select(Contract.vendor_id).where(
                visible_clause(u), Contract.vendor_id.is_not(None)
            )
        ).all()
        created = session.exec(
            select(Vendor.id).where(Vendor.org_id == u.org_id, Vendor.created_by == u.user_id)
        ).all()
        return set(via_contracts) | set(created)

    def _get(self, session, vendor_id: str) -> Vendor:
        v = session.get(Vendor, vendor_id)
        if v is None or v.org_id != self.user.org_id:
            raise NotFound("Vendor not found.")
        if vendor_id not in self._visible_vendor_ids(session):
            raise NotFound("Vendor not found.")
        return v

    # -- resolution (used by the contract service) -----------------------
    def resolve(self, session, name: str, force_new: bool = False) -> VendorResolution:
        name = (name or "").strip()
        norm = normalize_name(name)
        if not norm:
            raise ValidationError("Vendor name is required.")
        org = self.user.org_id

        # Exact normalised match or alias: always reuse (reveals nothing new
        # to the caller, even if the vendor is only used by hidden contracts).
        v = session.exec(
            select(Vendor).where(Vendor.org_id == org, Vendor.normalized_name == norm)
        ).first()
        if v:
            return VendorResolution(vendor=v)
        alias = session.exec(
            select(VendorAlias).where(
                VendorAlias.org_id == org, VendorAlias.normalized_alias == norm
            )
        ).first()
        if alias:
            return VendorResolution(vendor=session.get(Vendor, alias.vendor_id))

        # Near matches are only suggested among vendors the caller may see.
        if not force_new:
            visible = self._visible_vendor_ids(session)
            similar: dict[str, Vendor] = {}
            for cand in session.exec(select(Vendor).where(Vendor.org_id == org)).all():
                if cand.id in visible and (
                    SequenceMatcher(None, norm, cand.normalized_name).ratio()
                    >= SIMILAR_VENDOR_THRESHOLD
                ):
                    similar[cand.id] = cand
            for al in session.exec(select(VendorAlias).where(VendorAlias.org_id == org)).all():
                if al.vendor_id in visible and al.vendor_id not in similar:
                    if (
                        SequenceMatcher(None, norm, al.normalized_alias).ratio()
                        >= SIMILAR_VENDOR_THRESHOLD
                    ):
                        similar[al.vendor_id] = session.get(Vendor, al.vendor_id)
            if similar:
                return VendorResolution(needs_confirmation=True, similar=list(similar.values()))

        v = Vendor(org_id=org, name=name, normalized_name=norm, created_by=self.user.user_id)
        session.add(v)
        session.flush()
        audit(session, self.user, "vendor.create", "vendor", v.id)
        return VendorResolution(vendor=v, created=True)

    # -- public API ------------------------------------------------------
    def create(self, name: str, website: str = "", notes: str = "", force_new: bool = False):
        with self._session() as session:
            res = self.resolve(session, name, force_new=force_new)
            if res.needs_confirmation:
                names = ", ".join(v.name for v in res.similar)
                raise Conflict(
                    f"Similar vendor(s) already exist: {names}. Use the existing "
                    "vendor, or confirm that this is a different vendor."
                )
            if not res.created:
                raise Conflict(f"Vendor '{res.vendor.name}' already exists.")
            res.vendor.website = (website or "").strip() or None
            res.vendor.notes = (notes or "").strip() or None
            session.commit()
            return res.vendor

    def list(self, search: str = "") -> list[VendorSummary]:
        u = self.user
        with self._session() as session:
            visible = self._visible_vendor_ids(session)
            vendors = session.exec(select(Vendor).where(Vendor.org_id == u.org_id)).all()
            aliases: dict[str, list[str]] = {}
            for a in session.exec(select(VendorAlias).where(VendorAlias.org_id == u.org_id)).all():
                aliases.setdefault(a.vendor_id, []).append(a.alias)
            counts: dict[str, int] = {}
            for vid in session.exec(
                select(Contract.vendor_id).where(
                    visible_clause(u),
                    Contract.role == ROLE_HISTORICAL,
                    Contract.vendor_id.is_not(None),
                )
            ).all():
                counts[vid] = counts.get(vid, 0) + 1
            q = (search or "").strip().lower()
            out = []
            for v in vendors:
                if v.id not in visible:
                    continue
                al = sorted(aliases.get(v.id, []))
                if q and q not in v.name.lower() and not any(q in a.lower() for a in al):
                    continue
                out.append(
                    VendorSummary(v.id, v.name, al, counts.get(v.id, 0), v.website, v.notes, v.created_at)
                )
            return sorted(out, key=lambda s: s.name.lower())

    def get(self, vendor_id: str) -> VendorSummary:
        for s in self.list():
            if s.id == vendor_id:
                return s
        raise NotFound("Vendor not found.")

    def update(self, vendor_id: str, *, name=None, website=None, notes=None, aliases=None):
        with self._session() as session:
            v = self._get(session, vendor_id)
            changed = []
            if name is not None and name.strip() and name.strip() != v.name:
                norm = normalize_name(name)
                clash = session.exec(
                    select(Vendor).where(
                        Vendor.org_id == v.org_id,
                        Vendor.normalized_name == norm,
                        Vendor.id != v.id,
                    )
                ).first()
                if clash:
                    raise Conflict("Another vendor already uses that name. Use merge instead.")
                old = v.name
                v.name, v.normalized_name = name.strip(), norm
                if normalize_name(old) != norm:  # keep the previous name as an alias
                    self._set_alias(session, v, old)
                changed.append("name")
            if website is not None:
                v.website = website.strip() or None
                changed.append("website")
            if notes is not None:
                v.notes = notes.strip() or None
                changed.append("notes")
            if aliases is not None:
                self._replace_aliases(session, v, aliases)
                changed.append("aliases")
            v.updated_at = utcnow()
            audit(session, self.user, "vendor.update", "vendor", v.id, {"fields": changed})
            session.commit()
            return v

    def _set_alias(self, session, v: Vendor, alias: str):
        norm = normalize_name(alias)
        if not norm or norm == v.normalized_name:
            return
        existing = session.exec(
            select(VendorAlias).where(
                VendorAlias.org_id == v.org_id, VendorAlias.normalized_alias == norm
            )
        ).first()
        if existing:
            if existing.vendor_id != v.id:
                raise Conflict("That alias already belongs to another vendor.")
            return
        clash = session.exec(
            select(Vendor).where(Vendor.org_id == v.org_id, Vendor.normalized_name == norm)
        ).first()
        if clash and clash.id != v.id:
            raise Conflict("That alias is already the name of another vendor.")
        session.add(VendorAlias(org_id=v.org_id, vendor_id=v.id, alias=alias.strip(), normalized_alias=norm))
        session.flush()

    def _replace_aliases(self, session, v: Vendor, aliases: list[str]):
        wanted = {}
        for a in aliases:
            a = (a or "").strip()
            if a and normalize_name(a):
                wanted[normalize_name(a)] = a
        for old in session.exec(select(VendorAlias).where(VendorAlias.vendor_id == v.id)).all():
            if old.normalized_alias not in wanted:
                session.delete(old)
        session.flush()
        for a in wanted.values():
            self._set_alias(session, v, a)

    def merge(self, source_id: str, target_id: str) -> Vendor:
        if not self.user.is_admin:
            raise Forbidden("Only administrators can merge vendors.")
        if source_id == target_id:
            raise ValidationError("Choose two different vendors.")
        with self._session() as session:
            src = self._get(session, source_id)
            tgt = self._get(session, target_id)
            moved = 0
            for c in session.exec(select(Contract).where(Contract.vendor_id == src.id)).all():
                c.vendor_id = tgt.id
                c.updated_at = utcnow()
                moved += 1
            src_aliases = session.exec(select(VendorAlias).where(VendorAlias.vendor_id == src.id)).all()
            names = [src.name] + [a.alias for a in src_aliases]
            for a in src_aliases:
                session.delete(a)
            src_name_id = src.id
            session.delete(src)
            session.flush()
            for n in names:
                self._set_alias(session, tgt, n)
            audit(session, self.user, "vendor.merge", "vendor", tgt.id,
                  {"merged_vendor_id": src_name_id, "contracts_moved": moved})
            session.commit()
            return tgt

    def delete(self, vendor_id: str) -> None:
        if not self.user.is_admin:
            raise Forbidden("Only administrators can delete vendors.")
        with self._session() as session:
            v = self._get(session, vendor_id)
            n = session.exec(select(Contract.id).where(Contract.vendor_id == v.id)).first()
            if n:
                raise Conflict("This vendor has contracts. Merge it into another vendor instead.")
            for a in session.exec(select(VendorAlias).where(VendorAlias.vendor_id == v.id)).all():
                session.delete(a)
            session.delete(v)
            audit(session, self.user, "vendor.delete", "vendor", vendor_id)
            session.commit()
