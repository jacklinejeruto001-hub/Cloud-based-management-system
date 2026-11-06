"""
Role-Based Access Control (RBAC) matrix.

Every capability in the system is a string key. The value is the tuple of roles
allowed to use it. Views are protected with @permission_required("key") and
templates with {% if user|can:"key" %}.
"""

ADMIN = "ADMIN"
MANAGER = "MANAGER"
OPERATIONS = "OPERATIONS"
DISPATCH = "DISPATCH"

PERMISSIONS = {
    # shipment records
    "shipment.edit": (ADMIN, MANAGER, OPERATIONS),       # create / update shipment details
    "shipment.delete": (ADMIN, MANAGER),                 # archive / restore (soft delete)
    "status.update": (ADMIN, MANAGER, OPERATIONS, DISPATCH),
    # documents
    "document.upload": (ADMIN, MANAGER, OPERATIONS),
    "document.delete": (ADMIN, MANAGER),
    # delivery confirmation (proof of delivery)
    "delivery.confirm": (ADMIN, MANAGER, OPERATIONS, DISPATCH),
    # data-quality management
    "duplicates.review": (ADMIN, MANAGER),
    # management reporting / oversight
    "reports.view": (ADMIN, MANAGER),
    "audit.view": (ADMIN, MANAGER),
    # user administration
    "users.manage": (ADMIN,),
}


def role_can(role, perm):
    return role in PERMISSIONS.get(perm, ())
