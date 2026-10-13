"""
Central definition of what each Role can do, expressed as Django
Group -> Permission mappings. One Django Group per CustomUser.Role, named
identically to the role value, so `user.groups.add(...)` and role-based
checks stay in sync.

Permission codenames follow Django's default convention:
    {add|change|delete|view}_{model_name}

Run via: python manage.py seed_roles
"""

from accounts.models import CustomUser

# app_label.model_name -> which of the four standard perms to grant.
# "view" is implied whenever any other permission is granted.
ROLE_PERMISSIONS = {
    CustomUser.Role.ADMIN: {
        # Admins get full control over every model; granted via is_superuser
        # in the seed command rather than enumerated here.
        "_is_superuser_role": True,
    },
    CustomUser.Role.HR: {
        "employees.employee": ["add", "change", "view"],
        "employees.faceencoding": ["add", "change", "view"],
        "locations.department": ["view"],
        "attendance.shift": ["add", "change", "view"],
        "attendance.employeeshiftassignment": ["add", "change", "view"],
        "attendance.holiday": ["add", "change", "view"],
        "attendance.leaverequest": ["add", "change", "view"],
        "attendance.attendance": ["view", "change"],
        "attendance.attendancecorrectionrequest": ["add", "change", "view"],
    },
    CustomUser.Role.AREA_MANAGER: {
        "locations.area": ["view"],
        "locations.project": ["view", "change"],
        "locations.mine": ["view", "change"],
        "locations.department": ["view"],
        "employees.employee": ["view"],
        "attendance.attendance": ["view"],
        "attendance.attendancecorrectionrequest": ["view", "change"],
        "attendance.leaverequest": ["view", "change"],
    },
    CustomUser.Role.DEPT_HEAD: {
        "employees.employee": ["view"],
        "attendance.attendance": ["view"],
        "attendance.attendancecorrectionrequest": ["add", "view"],
        "attendance.leaverequest": ["view", "change"],
    },
    CustomUser.Role.SECURITY: {
        "attendance.camera": ["view", "change"],
        "attendance.camerahealthlog": ["view"],
        "attendance.attendance": ["view"],
    },
}
