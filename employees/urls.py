from django.urls import path

from employees import views

app_name = "employees"

urlpatterns = [
    path("", views.employee_list, name="list"),
    path("new/", views.employee_create, name="create"),
    path("<uuid:employee_id>/", views.employee_detail, name="detail"),
    path("<uuid:employee_id>/enroll/", views.employee_enroll, name="enroll"),
    path("options/projects/<int:area_id>/", views.projects_for_area, name="projects_for_area"),
    path("options/mines/<int:project_id>/", views.mines_for_project, name="mines_for_project"),
    path("options/departments/<int:mine_id>/", views.departments_for_mine, name="departments_for_mine"),
]
