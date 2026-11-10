"""Row-level authorisation. Every query that returns contract data is built
from ``visible_clause`` so users can never read, count or sum contracts they
are not allowed to see."""

from dealwise.db.models import AuditLog, Contract
from dealwise.security.context import UserContext
from sqlalchemy import and_, or_


def visible_clause(user: UserContext):
    clauses = [Contract.org_id == user.org_id]
    if not user.is_admin:
        clauses.append(
            or_(Contract.visibility == "organization", Contract.owner_id == user.user_id)
        )
    return and_(*clauses)


def can_view(user: UserContext, c: Contract) -> bool:
    if c.org_id != user.org_id:
        return False
    return user.is_admin or c.visibility == "organization" or c.owner_id == user.user_id


def can_edit(user: UserContext, c: Contract) -> bool:
    return c.org_id == user.org_id and (user.is_admin or c.owner_id == user.user_id)


def audit(session, user: UserContext, action: str, entity_type: str, entity_id, detail=None):
    """Record an event. ``detail`` must contain ids / field names only."""
    session.add(
        AuditLog(
            org_id=user.org_id,
            user_id=user.user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )
    )
