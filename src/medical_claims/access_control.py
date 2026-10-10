"""Compatibility helpers backed by the authoritative role-permission seed data."""

from __future__ import annotations

from enum import Enum

from medical_claims.database_migrations import PERMISSIONS_BY_ROLE


class RoleCode(str, Enum):
    CLAIM = "CLAIM"
    MEDICAL = "MEDICAL"
    SERVICE = "SERVICE"
    CUSTOMER = "CUSTOMER"
    SYSTEM_ADMIN = "SYSTEM_ADMIN"


class Permission(str, Enum):
    CASE_VIEW = "CASE_VIEW"
    CARE_REVIEW = "CARE_REVIEW"
    FOLLOWUP_HANDLE = "FOLLOWUP_HANDLE"
    OWN_CARE_VIEW = "OWN_CARE_VIEW"
    USER_MANAGE = "USER_MANAGE"
    AUDIT_VIEW = "AUDIT_VIEW"


def has_permission(role: RoleCode | str, permission: Permission | str) -> bool:
    """Return a backend-usable authorization decision for a single role."""

    try:
        role_code = RoleCode(role)
        permission_code = Permission(permission)
    except ValueError:
        return False
    return permission_code.value in PERMISSIONS_BY_ROLE[role_code.value]
