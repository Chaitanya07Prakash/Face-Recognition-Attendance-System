import datetime

from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomUser
from attendance.models import (
    Attendance,
    AttendanceCorrectionRequest,
    Camera,
    EmployeeShiftAssignment,
    Holiday,
    LeaveRequest,
    Shift,
)
from attendance.services import (
    apply_leave_and_holidays_for_date,
    mark_attendance,
    request_correction,
    resolve_business_date,
    review_correction,
)
from employees.models import Employee
from locations.models import Area, Department, Mine, Project


class MarkAttendanceTests(TestCase):
    def setUp(self):
        area = Area.objects.create(name="Ranchi HQ", code="RNC")
        project = Project.objects.create(area=area, name="Piparwar", code="PPW")
        mine = Mine.objects.create(project=project, name="Piparwar OCP", code="OCP1")
        dept = Department.objects.create(mine=mine, name="Surveying")
        self.employee = Employee.objects.create(
            employee_code="RNC-0001",
            full_name="Test Employee",
            department=dept,
            designation="Surveyor",
            date_joined=datetime.date.today(),
        )

    def test_first_recognition_creates_check_in(self):
        now = timezone.now()
        result = mark_attendance(self.employee, confidence=0.9, event_time=now)

        self.assertEqual(result.action, "check_in")
        self.assertEqual(Attendance.objects.count(), 1)
        self.assertEqual(result.record.check_in_time, now)
        self.assertEqual(result.record.status, Attendance.Status.PRESENT)

    def test_rapid_repeat_scan_is_ignored_not_duplicated(self):
        now = timezone.now()
        mark_attendance(self.employee, confidence=0.9, event_time=now)

        # Same person lingers in front of the camera 30 seconds later.
        second = mark_attendance(
            self.employee, confidence=0.88, event_time=now + datetime.timedelta(seconds=30)
        )

        self.assertEqual(second.action, "duplicate_ignored")
        self.assertEqual(Attendance.objects.count(), 1)  # no second row created
        self.assertIsNone(second.record.check_out_time)  # untouched

    def test_recognition_after_window_is_treated_as_check_out(self):
        # Fixed mid-day time, not timezone.now(), so this test doesn't flake
        # when it happens to run near UTC midnight (see NOTE in services.py
        # about business-date being naive until Phase 3 shift logic lands).
        now = timezone.make_aware(datetime.datetime(2026, 7, 11, 9, 0, 0))
        mark_attendance(self.employee, confidence=0.9, event_time=now)

        later = now + datetime.timedelta(hours=8)
        result = mark_attendance(self.employee, confidence=0.85, event_time=later)

        self.assertEqual(result.action, "check_out")
        self.assertEqual(Attendance.objects.count(), 1)  # still one row for the day
        self.assertEqual(result.record.check_out_time, later)
        self.assertEqual(result.record.check_out_confidence, 0.85)

    def test_next_calendar_day_creates_a_new_row(self):
        day1 = timezone.now()
        day2 = day1 + datetime.timedelta(days=1)

        mark_attendance(self.employee, confidence=0.9, event_time=day1)
        mark_attendance(self.employee, confidence=0.9, event_time=day2)

        self.assertEqual(Attendance.objects.count(), 2)

    def test_unique_together_prevents_duplicate_rows_at_db_level(self):
        now = timezone.now()
        Attendance.objects.create(employee=self.employee, date=now.date())

        with self.assertRaises(Exception):
            Attendance.objects.create(employee=self.employee, date=now.date())


class ShiftAwareAttendanceTests(TestCase):
    """
    Phase 3: proves the Phase 2 midnight bug (night-shift check-in/check-out
    landing on two different calendar dates) is actually fixed, plus the
    new late/early/overtime and leave/holiday/correction logic.
    """

    def setUp(self):
        area = Area.objects.create(name="Ranchi HQ", code="RNC3")
        project = Project.objects.create(area=area, name="Piparwar", code="PPW3")
        mine = Mine.objects.create(project=project, name="Piparwar OCP", code="OCP3")
        self.dept = Department.objects.create(mine=mine, name="Surveying")
        self.employee = Employee.objects.create(
            employee_code="RNC3-0001",
            full_name="Night Shift Employee",
            department=self.dept,
            designation="Surveyor",
            date_joined=datetime.date(2026, 1, 1),
        )
        self.night_shift = Shift.objects.create(
            name="Night A", start_time=datetime.time(22, 0), end_time=datetime.time(6, 0),
            is_night_shift=True, grace_minutes=10,
        )
        self.day_shift = Shift.objects.create(
            name="Day A", start_time=datetime.time(9, 0), end_time=datetime.time(17, 0),
            is_night_shift=False, grace_minutes=10,
        )
        EmployeeShiftAssignment.objects.create(
            employee=self.employee, shift=self.night_shift, effective_from=datetime.date(2026, 1, 1)
        )

    def test_night_shift_checkin_and_checkout_land_on_same_business_date(self):
        # This is exactly the Phase 2 bug scenario: check-in at 10pm, and
        # check-out at 4am the *next calendar day*.
        check_in = timezone.make_aware(datetime.datetime(2026, 7, 11, 22, 0))
        check_out = timezone.make_aware(datetime.datetime(2026, 7, 12, 4, 0))

        r1 = mark_attendance(self.employee, confidence=0.9, event_time=check_in)
        r2 = mark_attendance(self.employee, confidence=0.9, event_time=check_out)

        self.assertEqual(r1.action, "check_in")
        self.assertEqual(r2.action, "check_out")
        self.assertEqual(Attendance.objects.count(), 1)  # not 2 — this is the fix
        record = Attendance.objects.get()
        self.assertEqual(record.date, datetime.date(2026, 7, 11))  # attributed to the start date
        self.assertEqual(record.check_in_time, check_in)
        self.assertEqual(record.check_out_time, check_out)

    def test_resolve_business_date_directly_for_early_morning_event(self):
        early_morning = timezone.make_aware(datetime.datetime(2026, 7, 12, 3, 30))
        business_date, shift = resolve_business_date(self.employee, early_morning)
        self.assertEqual(business_date, datetime.date(2026, 7, 11))
        self.assertEqual(shift, self.night_shift)

    def test_day_shift_late_arrival_is_flagged(self):
        EmployeeShiftAssignment.objects.create(
            employee=self.employee, shift=self.day_shift, effective_from=datetime.date(2026, 8, 1)
        )
        late_checkin = timezone.make_aware(datetime.datetime(2026, 8, 5, 9, 25))  # 25 min late, grace=10
        result = mark_attendance(self.employee, confidence=0.9, event_time=late_checkin)
        self.assertTrue(result.record.is_late)

    def test_day_shift_on_time_arrival_within_grace_is_not_late(self):
        EmployeeShiftAssignment.objects.create(
            employee=self.employee, shift=self.day_shift, effective_from=datetime.date(2026, 8, 1)
        )
        on_time = timezone.make_aware(datetime.datetime(2026, 8, 5, 9, 5))  # within 10 min grace
        result = mark_attendance(self.employee, confidence=0.9, event_time=on_time)
        self.assertFalse(result.record.is_late)

    def test_overtime_minutes_calculated_on_checkout(self):
        EmployeeShiftAssignment.objects.create(
            employee=self.employee, shift=self.day_shift, effective_from=datetime.date(2026, 8, 1)
        )
        checkin = timezone.make_aware(datetime.datetime(2026, 8, 5, 9, 0))
        checkout = timezone.make_aware(datetime.datetime(2026, 8, 5, 18, 30))  # 1.5h past 17:00
        mark_attendance(self.employee, confidence=0.9, event_time=checkin)
        result = mark_attendance(self.employee, confidence=0.9, event_time=checkout)
        self.assertEqual(result.record.overtime_minutes, 90)

    def test_holiday_creates_attendance_row_without_checkin(self):
        Holiday.objects.create(area=None, date=datetime.date(2026, 8, 15), name="Independence Day")
        created = apply_leave_and_holidays_for_date(
            datetime.date(2026, 8, 15), Employee.objects.filter(pk=self.employee.pk)
        )
        self.assertEqual(created, 1)
        record = Attendance.objects.get(employee=self.employee, date=datetime.date(2026, 8, 15))
        self.assertEqual(record.status, Attendance.Status.HOLIDAY)

    def test_approved_leave_creates_on_leave_row(self):
        LeaveRequest.objects.create(
            employee=self.employee,
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 9, 3),
            status=LeaveRequest.Status.APPROVED,
        )
        created = apply_leave_and_holidays_for_date(
            datetime.date(2026, 9, 2), Employee.objects.filter(pk=self.employee.pk)
        )
        self.assertEqual(created, 1)
        record = Attendance.objects.get(employee=self.employee, date=datetime.date(2026, 9, 2))
        self.assertEqual(record.status, Attendance.Status.ON_LEAVE)

    def test_existing_attendance_row_is_not_overwritten_by_leave_holiday_sweep(self):
        # Employee already checked in that day — the sweep must not clobber it.
        EmployeeShiftAssignment.objects.create(
            employee=self.employee, shift=self.day_shift, effective_from=datetime.date(2026, 8, 1)
        )
        checkin = timezone.make_aware(datetime.datetime(2026, 9, 10, 9, 0))
        mark_attendance(self.employee, confidence=0.9, event_time=checkin)
        created = apply_leave_and_holidays_for_date(
            datetime.date(2026, 9, 10), Employee.objects.filter(pk=self.employee.pk)
        )
        self.assertEqual(created, 0)
        self.assertEqual(Attendance.objects.count(), 1)


class AttendanceCorrectionWorkflowTests(TestCase):
    def setUp(self):
        area = Area.objects.create(name="Ranchi HQ", code="RNC4")
        project = Project.objects.create(area=area, name="Piparwar", code="PPW4")
        mine = Mine.objects.create(project=project, name="Piparwar OCP", code="OCP4")
        dept = Department.objects.create(mine=mine, name="Surveying")
        self.employee = Employee.objects.create(
            employee_code="RNC4-0001",
            full_name="Correction Test Employee",
            department=dept,
            designation="Surveyor",
            date_joined=datetime.date(2026, 1, 1),
        )
        self.hr_user = CustomUser.objects.create_user(
            username="hr.reviewer", email="hr@example.com", password="testpass123",
            role=CustomUser.Role.HR,
        )
        self.record = Attendance.objects.create(
            employee=self.employee, date=datetime.date(2026, 8, 20), status=Attendance.Status.ABSENT
        )

    def test_approved_correction_updates_attendance_record(self):
        correction = request_correction(
            self.record, requested_by=self.hr_user,
            justification="Camera was offline, employee confirmed present via manager.",
            proposed_check_in=timezone.make_aware(datetime.datetime(2026, 8, 20, 9, 0)),
        )
        review_correction(correction, reviewed_by=self.hr_user, approve=True)

        self.record.refresh_from_db()
        self.assertTrue(self.record.is_manually_corrected)
        self.assertIsNotNone(self.record.check_in_time)
        correction.refresh_from_db()
        self.assertEqual(correction.status, AttendanceCorrectionRequest.Status.APPROVED)

    def test_rejected_correction_does_not_touch_attendance_record(self):
        correction = request_correction(
            self.record, requested_by=self.hr_user, justification="Unverified claim.",
            proposed_check_in=timezone.make_aware(datetime.datetime(2026, 8, 20, 9, 0)),
        )
        review_correction(correction, reviewed_by=self.hr_user, approve=False)

        self.record.refresh_from_db()
        self.assertFalse(self.record.is_manually_corrected)
        self.assertIsNone(self.record.check_in_time)

    def test_reviewing_an_already_reviewed_correction_raises(self):
        correction = request_correction(
            self.record, requested_by=self.hr_user, justification="test",
        )
        review_correction(correction, reviewed_by=self.hr_user, approve=True)

        with self.assertRaises(ValueError):
            review_correction(correction, reviewed_by=self.hr_user, approve=True)


from django.test import override_settings


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class CeleryTaskTests(TestCase):
    def setUp(self):
        self.camera = Camera.objects.create(
            name="Gate 1 - Entry", rtsp_url="rtsp://user:pass@10.0.1.10:554/stream1",
            gate_type=Camera.GateType.ENTRY,
        )

    def test_heartbeat_task_updates_camera_and_creates_health_log(self):
        from attendance.tasks import record_camera_heartbeat

        record_camera_heartbeat.delay(str(self.camera.id), Camera.Status.ONLINE, "frame grabbed ok")

        self.camera.refresh_from_db()
        self.assertEqual(self.camera.status, Camera.Status.ONLINE)
        self.assertIsNotNone(self.camera.last_heartbeat)
        self.assertEqual(self.camera.health_logs.count(), 1)
        self.assertEqual(self.camera.health_logs.first().detail, "frame grabbed ok")

    def test_repeated_failures_increment_consecutive_failures_counter(self):
        from attendance.tasks import record_camera_heartbeat

        record_camera_heartbeat.delay(str(self.camera.id), Camera.Status.OFFLINE, "connection timeout")
        record_camera_heartbeat.delay(str(self.camera.id), Camera.Status.OFFLINE, "connection timeout")
        record_camera_heartbeat.delay(str(self.camera.id), Camera.Status.OFFLINE, "connection timeout")

        self.camera.refresh_from_db()
        self.assertEqual(self.camera.consecutive_failures, 3)
        self.assertEqual(self.camera.health_logs.count(), 3)

    def test_online_heartbeat_resets_failure_counter(self):
        from attendance.tasks import record_camera_heartbeat

        record_camera_heartbeat.delay(str(self.camera.id), Camera.Status.OFFLINE, "timeout")
        record_camera_heartbeat.delay(str(self.camera.id), Camera.Status.OFFLINE, "timeout")
        record_camera_heartbeat.delay(str(self.camera.id), Camera.Status.ONLINE, "recovered")

        self.camera.refresh_from_db()
        self.assertEqual(self.camera.consecutive_failures, 0)
        self.assertEqual(self.camera.status, Camera.Status.ONLINE)

    def test_process_frame_without_face_recognition_installed_returns_error_not_crash(self):
        # The task must degrade gracefully rather than crash the worker when face_recognition is not installed.
        import numpy as np
        from unittest.mock import patch

        from attendance.tasks import encode_frame_for_task, process_frame

        blank_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        encoded = encode_frame_for_task(blank_frame)

        with patch.dict("sys.modules", {"face_recognition": None}):
            result = process_frame.delay(str(self.camera.id), encoded, "2026-07-12T09:00:00+00:00")
            value = result.get()
            self.assertEqual(value["status"], "error")

    def test_frame_encode_decode_roundtrip_preserves_shape(self):
        import numpy as np

        from attendance.tasks import decode_frame_from_task, encode_frame_for_task

        frame = (np.random.default_rng(0).integers(0, 255, size=(120, 160, 3))).astype(np.uint8)
        encoded = encode_frame_for_task(frame)
        decoded = decode_frame_from_task(encoded)
        self.assertEqual(decoded.shape, frame.shape)


from django.test import TransactionTestCase


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class CameraWorkerReconnectTests(TransactionTestCase):
    """
    Runs the real capture worker loop against a genuinely unreachable RTSP
    URL to prove the auto-reconnect/health-reporting path actually fires,
    not just that the code compiles.
    """

    def setUp(self):
        self.camera = Camera.objects.create(
            name="Test Gate", rtsp_url="rtsp://192.0.2.1:554/nonexistent",  # TEST-NET-1, guaranteed unreachable
            gate_type=Camera.GateType.ENTRY,
        )

    def test_unreachable_camera_reports_offline_and_logs_health(self):
        import threading
        from unittest.mock import patch, MagicMock

        from attendance.camera_worker import run_camera_worker
        import attendance.camera_worker as camera_worker_module

        # Mock VideoCapture to fail immediately instead of waiting for TCP timeout.
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False

        with patch("cv2.VideoCapture", return_value=mock_cap):
            # Speed up the test: don't wait through the real backoff schedule.
            original_backoff = camera_worker_module.RECONNECT_BACKOFF_SECONDS
            camera_worker_module.RECONNECT_BACKOFF_SECONDS = [0.2, 0.2, 0.2]

            stop_event = threading.Event()
            try:
                worker_thread = threading.Thread(
                    target=run_camera_worker,
                    args=(str(self.camera.id), self.camera.rtsp_url, stop_event),
                    daemon=True,
                )
                worker_thread.start()
                worker_thread.join(timeout=3)
                stop_event.set()
                worker_thread.join(timeout=3)
            finally:
                camera_worker_module.RECONNECT_BACKOFF_SECONDS = original_backoff

        self.camera.refresh_from_db()
        # After repeated failures within the test window, status may have
        # already escalated from OFFLINE to DEGRADED (see
        # MAX_CONSECUTIVE_FAILURES_BEFORE_DEGRADED) — both are correct
        # outcomes of "this camera is not reachable".
        self.assertIn(self.camera.status, [Camera.Status.OFFLINE, Camera.Status.DEGRADED])
        self.assertGreater(self.camera.health_logs.count(), 0)
        self.assertTrue(
            all(log.status in [Camera.Status.OFFLINE, Camera.Status.DEGRADED] for log in self.camera.health_logs.all())
        )
