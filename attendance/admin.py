from django.contrib import admin

from attendance.models import (
    Attendance,
    AttendanceCorrectionRequest,
    EmployeeShiftAssignment,
    Holiday,
    LeaveRequest,
    Shift,
)


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("name", "start_time", "end_time", "is_night_shift", "grace_minutes")
    list_filter = ("is_night_shift",)


@admin.register(EmployeeShiftAssignment)
class EmployeeShiftAssignmentAdmin(admin.ModelAdmin):
    list_display = ("employee", "shift", "effective_from", "effective_to")
    list_filter = ("shift",)
    search_fields = ("employee__employee_code", "employee__full_name")
    autocomplete_fields = ("employee",)


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ("name", "date", "area")
    list_filter = ("area",)
    date_hierarchy = "date"


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ("employee", "start_date", "end_date", "status", "approved_by")
    list_filter = ("status",)
    search_fields = ("employee__employee_code", "employee__full_name")
    autocomplete_fields = ("employee",)


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = (
        "employee", "date", "shift", "check_in_time", "check_out_time",
        "status", "is_late", "is_early_departure", "overtime_minutes", "is_manually_corrected",
    )
    list_filter = ("status", "is_late", "is_early_departure", "is_manually_corrected", "shift", "date")
    search_fields = ("employee__employee_code", "employee__full_name")
    date_hierarchy = "date"


@admin.register(AttendanceCorrectionRequest)
class AttendanceCorrectionRequestAdmin(admin.ModelAdmin):
    list_display = ("attendance", "requested_by", "status", "reviewed_by", "reviewed_at")
    list_filter = ("status",)
    readonly_fields = ("requested_by", "reviewed_by", "reviewed_at")
