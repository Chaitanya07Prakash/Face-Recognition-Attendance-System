import os
import shutil
import tempfile

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from employees.forms import EmployeeForm
from employees.models import Employee
from locations.models import Department, Mine, Project

MIN_ENROLLMENT_PHOTOS = 3


@login_required
def employee_list(request):
    employees = Employee.objects.select_related(
        "department", "department__mine", "department__mine__project", "department__mine__project__area"
    ).order_by("full_name")

    query = request.GET.get("q", "").strip()
    if query:
        employees = employees.filter(
            Q(full_name__icontains=query) | Q(employee_code__icontains=query)
        )

    context = {"employees": employees, "query": query}
    # HTMX live-search: swap just the table body, not the whole page.
    if request.headers.get("HX-Request"):
        return render(request, "employees/_employee_table_rows.html", context)
    return render(request, "employees/employee_list.html", context)


@login_required
@permission_required("employees.add_employee", raise_exception=True)
def employee_create(request):
    if request.method == "POST":
        form = EmployeeForm(request.POST)
        if form.is_valid():
            employee = form.save()
            messages.success(request, f"{employee.full_name} registered. Now enroll their face samples.")
            return redirect("employees:enroll", employee_id=employee.id)
    else:
        form = EmployeeForm()
    return render(request, "employees/employee_form.html", {"form": form})


@login_required
def employee_detail(request, employee_id):
    employee = get_object_or_404(
        Employee.objects.select_related("department__mine__project__area"), id=employee_id
    )
    encodings = employee.face_encodings.filter(is_active=True).order_by("-created_at")
    recent_attendance = employee.attendance_records.order_by("-date")[:10]
    return render(request, "employees/employee_detail.html", {
        "employee": employee, "encodings": encodings, "recent_attendance": recent_attendance,
    })


@login_required
@permission_required("employees.add_employee", raise_exception=True)
def employee_enroll(request, employee_id):
    employee = get_object_or_404(Employee, id=employee_id)

    if request.method == "POST":
        uploaded_files = request.FILES.getlist("photos")

        if len(uploaded_files) < MIN_ENROLLMENT_PHOTOS:
            messages.error(
                request,
                f"Please provide at least {MIN_ENROLLMENT_PHOTOS} photos "
                f"(got {len(uploaded_files)}). More samples = better accuracy.",
            )
            return render(request, "employees/employee_enroll.html", {"employee": employee})

        tmp_dir = tempfile.mkdtemp(prefix="face_enroll_")
        try:
            tmp_paths = []
            for uploaded in uploaded_files:
                path = os.path.join(tmp_dir, uploaded.name)
                with open(path, "wb") as out:
                    for chunk in uploaded.chunks():
                        out.write(chunk)
                tmp_paths.append(path)

            try:
                from employees.recognition import enroll_employee_images
                created, rejected = enroll_employee_images(employee, tmp_paths)
            except RuntimeError as exc:
                # face_recognition/dlib not installed in this environment.
                messages.error(request, str(exc))
                return render(request, "employees/employee_enroll.html", {"employee": employee})

            if created:
                messages.success(request, f"Enrolled {created} face sample(s) for {employee.full_name}.")
            for path, reason in rejected:
                messages.warning(request, f"{os.path.basename(path)}: {reason}")

            if created == 0:
                return render(request, "employees/employee_enroll.html", {"employee": employee})

            return redirect("employees:detail", employee_id=employee.id)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return render(request, "employees/employee_enroll.html", {"employee": employee})


# --- Cascading dropdown endpoints (HTMX partials: return <option> tags only) ---

@login_required
def projects_for_area(request, area_id):
    projects = Project.objects.filter(area_id=area_id).order_by("name")
    return render(request, "employees/_options.html", {"objects": projects})


@login_required
def mines_for_project(request, project_id):
    mines = Mine.objects.filter(project_id=project_id).order_by("name")
    return render(request, "employees/_options.html", {"objects": mines})


@login_required
def departments_for_mine(request, mine_id):
    departments = Department.objects.filter(mine_id=mine_id).order_by("name")
    return render(request, "employees/_options.html", {"objects": departments})
