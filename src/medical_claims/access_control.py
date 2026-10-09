"""Fixed prototype permission matrix; authentication remains an external concern."""

from __future__ import annotations

from enum import Enum


class RoleCode(str, Enum):
    PATIENT = "patient"
    CLAIMS_SERVICE = "claims_service"
    MEDICAL_REVIEWER = "medical_reviewer"
    OPERATIONS = "operations"
    SYSTEM_ADMIN = "system_admin"


class Permission(str, Enum):
    VIEW_OWN_CARE = "view_own_care"
    SUBMIT_OWN_FEEDBACK = "submit_own_feedback"
    VIEW_CLAIM_SERVICE_SUMMARY = "view_claim_service_summary"
    MANAGE_FOLLOW_UP_TASK = "manage_follow_up_task"
    VIEW_DEIDENTIFIED_MEDICAL_RECORD = "view_deidentified_medical_record"
    REVIEW_CARE_GUIDE = "review_care_guide"
    VIEW_AGGREGATE_ANALYTICS = "view_aggregate_analytics"
    MANAGE_USERS_AND_ROLES = "manage_users_and_roles"
    VIEW_AUDIT_METADATA = "view_audit_metadata"


ROLE_PERMISSIONS: dict[RoleCode, frozenset[Permission]] = {
    RoleCode.PATIENT: frozenset({
        Permission.VIEW_OWN_CARE,
        Permission.SUBMIT_OWN_FEEDBACK,
    }),
    RoleCode.CLAIMS_SERVICE: frozenset({
        Permission.VIEW_CLAIM_SERVICE_SUMMARY,
        Permission.MANAGE_FOLLOW_UP_TASK,
    }),
    RoleCode.MEDICAL_REVIEWER: frozenset({
        Permission.VIEW_DEIDENTIFIED_MEDICAL_RECORD,
        Permission.REVIEW_CARE_GUIDE,
    }),
    RoleCode.OPERATIONS: frozenset({
        Permission.VIEW_AGGREGATE_ANALYTICS,
    }),
    RoleCode.SYSTEM_ADMIN: frozenset({
        Permission.MANAGE_USERS_AND_ROLES,
        Permission.VIEW_AUDIT_METADATA,
    }),
}


def has_permission(role: RoleCode | str, permission: Permission | str) -> bool:
    """Return a backend-usable authorization decision for a single role."""

    try:
        role_code = RoleCode(role)
        permission_code = Permission(permission)
    except ValueError:
        return False
    return permission_code in ROLE_PERMISSIONS[role_code]
