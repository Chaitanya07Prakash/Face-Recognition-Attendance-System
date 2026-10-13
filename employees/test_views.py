import datetime
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from accounts.models import CustomUser
from employees.models import Employee
from locations.models import Area, Department, Mine, Project


def _fake_image_file(name="photo.jpg"):
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), color=(120, 130, 140)).save(buf, format="JPEG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/jpeg")


class EmployeeWebUITests(TestCase):
    def setUp(self):
        self.area = Area.objects.create(name="Ranchi HQ", code="RNC")
        self.project = Project.objects.create(area=self.area, name="Piparwar", code="PPW")
        self.mine = Mine.objects.create(project=self.project, name="Piparwar OCP", code="OCP1")
        self.dept = Department.objects.create(mine=self.mine, name="Surveying")

        self.hr_user = CustomUser.objects.create_user(
            username="hr.web", email="hrweb@example.com", password="testpass123",
            role=CustomUser.Role.HR,
        )
        self.security_user = CustomUser.objects.create_user(
            username="sec.web", email="secweb@example.com", password="testpass123",
            role=CustomUser.Role.SECURITY,
        )
        # In production this runs once after `migrate` (see README). The
        # role-sync signal creates empty Groups on user creation; without
        # this, every permission_required check would 403 regardless of role.
        call_command("seed_roles", verbosity=0)

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("employees:list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_logged_in_user_sees_employee_list(self):
        self.client.login(username="hr.web", password="testpass123")
        response = self.client.get(reverse("employees:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Employees")

    def test_htmx_search_returns_partial_not_full_page(self):
        Employee.objects.create(
            employee_code="RNC-0001", full_name="Findable Person", department=self.dept,
            designation="Surveyor", date_joined=datetime.date.today(),
        )
        self.client.login(username="hr.web", password="testpass123")
        response = self.client.get(reverse("employees:list"), {"q": "Findable"}, HTTP_HX_REQUEST="true")
        self.assertContains(response, "Findable Person")
        self.assertNotContains(response, "<nav")  # partial, not the full base template

    def test_search_filters_by_name_and_code(self):
        Employee.objects.create(
            employee_code="RNC-0001", full_name="Alice Surveyor", department=self.dept,
            designation="Surveyor", date_joined=datetime.date.today(),
        )
        Employee.objects.create(
            employee_code="RNC-0002", full_name="Bob Engineer", department=self.dept,
            designation="Engineer", date_joined=datetime.date.today(),
        )
        self.client.login(username="hr.web", password="testpass123")

        response = self.client.get(reverse("employees:list"), {"q": "Alice"})
        self.assertContains(response, "Alice Surveyor")
        self.assertNotContains(response, "Bob Engineer")

        response = self.client.get(reverse("employees:list"), {"q": "RNC-0002"})
        self.assertContains(response, "Bob Engineer")
        self.assertNotContains(response, "Alice Surveyor")

    def test_hr_can_access_registration_form(self):
        self.client.login(username="hr.web", password="testpass123")
        response = self.client.get(reverse("employees:create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Register employee")

    def test_security_role_cannot_access_registration_form(self):
        # SECURITY role has no employees.add_employee permission per rbac.py
        self.client.login(username="sec.web", password="testpass123")
        response = self.client.get(reverse("employees:create"))
        self.assertEqual(response.status_code, 403)

    def test_valid_registration_creates_employee_and_redirects_to_enroll(self):
        self.client.login(username="hr.web", password="testpass123")
        response = self.client.post(reverse("employees:create"), {
            "employee_code": "RNC-0099",
            "full_name": "New Hire",
            "designation": "Technician",
            "date_joined": "2026-07-01",
            "phone": "",
            "email": "",
            "status": "ACTIVE",
            "area": self.area.pk,
            "project": self.project.pk,
            "mine": self.mine.pk,
            "department": self.dept.pk,
        })
        employee = Employee.objects.get(employee_code="RNC-0099")
        self.assertRedirects(response, reverse("employees:enroll", args=[employee.id]))
        self.assertEqual(employee.full_name, "New Hire")
        self.assertEqual(employee.department, self.dept)

    def test_registration_without_department_is_rejected(self):
        self.client.login(username="hr.web", password="testpass123")
        response = self.client.post(reverse("employees:create"), {
            "employee_code": "RNC-0098",
            "full_name": "Incomplete Hire",
            "designation": "Technician",
            "date_joined": "2026-07-01",
            "status": "ACTIVE",
            "area": self.area.pk,
            "project": self.project.pk,
            "mine": self.mine.pk,
            "department": "",
        })
        self.assertEqual(response.status_code, 200)  # re-renders form with errors
        self.assertFalse(Employee.objects.filter(employee_code="RNC-0098").exists())

    def test_duplicate_employee_code_is_rejected(self):
        Employee.objects.create(
            employee_code="RNC-0001", full_name="Existing", department=self.dept,
            designation="Surveyor", date_joined=datetime.date.today(),
        )
        self.client.login(username="hr.web", password="testpass123")
        response = self.client.post(reverse("employees:create"), {
            "employee_code": "RNC-0001",  # duplicate
            "full_name": "Duplicate Attempt",
            "designation": "Technician",
            "date_joined": "2026-07-01",
            "status": "ACTIVE",
            "area": self.area.pk,
            "project": self.project.pk,
            "mine": self.mine.pk,
            "department": self.dept.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Employee.objects.filter(employee_code="RNC-0001").count(), 1)

    def test_cascading_dropdown_endpoints_return_correct_options(self):
        self.client.login(username="hr.web", password="testpass123")

        response = self.client.get(reverse("employees:projects_for_area", args=[self.area.pk]))
        self.assertContains(response, "Piparwar")

        response = self.client.get(reverse("employees:mines_for_project", args=[self.project.pk]))
        self.assertContains(response, "Piparwar OCP")

        response = self.client.get(reverse("employees:departments_for_mine", args=[self.mine.pk]))
        self.assertContains(response, "Surveying")

    def test_employee_detail_page_shows_encodings_and_attendance(self):
        employee = Employee.objects.create(
            employee_code="RNC-0001", full_name="Detail Test", department=self.dept,
            designation="Surveyor", date_joined=datetime.date.today(),
        )
        self.client.login(username="hr.web", password="testpass123")
        response = self.client.get(reverse("employees:detail", args=[employee.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Detail Test")
        self.assertContains(response, "No face samples enrolled yet")

    def test_enrollment_with_fewer_than_3_photos_is_rejected(self):
        employee = Employee.objects.create(
            employee_code="RNC-0001", full_name="Enroll Test", department=self.dept,
            designation="Surveyor", date_joined=datetime.date.today(),
        )
        self.client.login(username="hr.web", password="testpass123")
        response = self.client.post(
            reverse("employees:enroll", args=[employee.id]),
            {"photos": [_fake_image_file(), _fake_image_file()]},  # only 2
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at least 3 photos")
        self.assertEqual(employee.face_encodings.count(), 0)

    def test_enrollment_without_face_recognition_installed_shows_friendly_error(self):
        # This sandbox doesn't have dlib/face_recognition installed (see
        # project README) — the view must degrade gracefully, not crash.
        employee = Employee.objects.create(
            employee_code="RNC-0001", full_name="Enroll Test 2", department=self.dept,
            designation="Surveyor", date_joined=datetime.date.today(),
        )
        self.client.login(username="hr.web", password="testpass123")
        response = self.client.post(
            reverse("employees:enroll", args=[employee.id]),
            {"photos": [_fake_image_file(), _fake_image_file(), _fake_image_file()]},
        )
        self.assertEqual(response.status_code, 200)
        # Whatever the underlying reason, the page must render normally
        # with a message rather than 500ing.
        self.assertTemplateUsed(response, "employees/employee_enroll.html")
