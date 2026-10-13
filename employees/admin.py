from django.contrib import admin

from employees.models import Employee, FaceEncoding


class FaceEncodingInline(admin.TabularInline):
    model = FaceEncoding
    fields = ("quality_score", "capture_source", "is_active", "created_at")
    readonly_fields = ("quality_score", "capture_source", "created_at")
    extra = 0


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("employee_code", "full_name", "department", "designation", "status", "date_joined")
    list_filter = ("status", "department__mine__project__area", "department")
    search_fields = ("employee_code", "full_name", "email", "phone")
    autocomplete_fields = ("department", "user_account")
    inlines = [FaceEncodingInline]
