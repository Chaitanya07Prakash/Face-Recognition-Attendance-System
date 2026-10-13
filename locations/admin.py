from django.contrib import admin

from locations.models import Area, Department, Mine, Project


@admin.register(Area)
class AreaAdmin(admin.ModelAdmin):
    list_display = ("name", "code")
    search_fields = ("name", "code")


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "area")
    list_filter = ("area",)
    search_fields = ("name", "code")


@admin.register(Mine)
class MineAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "project")
    list_filter = ("project__area", "project")
    search_fields = ("name", "code")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "mine", "head")
    list_filter = ("mine__project__area", "mine")
    search_fields = ("name",)
