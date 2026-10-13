import uuid

from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Shift(TimeStampedModel):
    name = models.CharField(max_length=100)  # e.g. "Night A"
    start_time = models.TimeField()
    end_time = models.TimeField()  # end < start implies the shift crosses midnight
    grace_minutes = models.PositiveSmallIntegerField(
        default=10, help_text="Minutes after start_time before a check-in counts as late"
    )
    is_night_shift = models.BooleanField(
        default=False,
        help_text="Set when end_time < start_time (shift crosses midnight). Drives "
        "business-date resolution in attendance.services.resolve_business_date.",
    )

    def __str__(self):
        return self.name

    def scheduled_minutes(self) -> int:
        """Duration of the shift in minutes, correctly handling overnight shifts."""
        start = self.start_time.hour * 60 + self.start_time.minute
        end = self.end_time.hour * 60 + self.end_time.minute
        if self.is_night_shift or end <= start:
            return (24 * 60 - start) + end
        return end - start


class EmployeeShiftAssignment(TimeStampedModel):
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="shift_assignments"
    )
    shift = models.ForeignKey(Shift, on_delete=models.PROTECT)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)  # null = ongoing

    class Meta:
        indexes = [models.Index(fields=["employee", "effective_from"])]
        ordering = ["-effective_from"]

    def __str__(self):
        return f"{self.employee.employee_code}: {self.shift.name} from {self.effective_from}"


class Holiday(TimeStampedModel):
    area = models.ForeignKey(
        "locations.Area",
        on_delete=models.CASCADE,
        related_name="holidays",
        null=True,
        blank=True,  # null = applies company-wide
    )
    date = models.DateField(db_index=True)
    name = models.CharField(max_length=150)

    class Meta:
        unique_together = [("area", "date")]

    def __str__(self):
        return f"{self.name} ({self.date})"


class LeaveRequest(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="leave_requests"
    )
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    approved_by = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leave_approvals",
    )

    class Meta:
        indexes = [models.Index(fields=["employee", "start_date", "end_date"])]

    def covers(self, date) -> bool:
        return self.start_date <= date <= self.end_date

    def __str__(self):
        return f"{self.employee.employee_code}: {self.start_date} to {self.end_date} ({self.status})"


class Attendance(TimeStampedModel):
    """
    One row per employee per business date. Duplicate-prevention is enforced
    at the database level via unique_together — repeated recognitions the
    same day update this row's check_in/check_out fields rather than
    creating new rows (see attendance.services.mark_attendance).

    business date (the `date` field) is resolved shift-aware as of Phase 3:
    for a night shift, the check-in and the early-morning check-out both
    land on the calendar date the shift *started*, not the date each event's
    wall-clock timestamp falls on. See services.resolve_business_date.
    """

    class Status(models.TextChoices):
        PRESENT = "PRESENT", "Present"
        ABSENT = "ABSENT", "Absent"
        HALF_DAY = "HALF_DAY", "Half day"
        ON_LEAVE = "ON_LEAVE", "On leave"
        HOLIDAY = "HOLIDAY", "Holiday"

    id = models.BigAutoField(primary_key=True)
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="attendance_records"
    )
    shift = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True)
    date = models.DateField(db_index=True)
    check_in_time = models.DateTimeField(null=True, blank=True)
    check_in_confidence = models.FloatField(
        null=True, blank=True, help_text="Recognition confidence at check-in, 0.0-1.0"
    )
    check_out_time = models.DateTimeField(null=True, blank=True)
    check_out_confidence = models.FloatField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ABSENT)
    is_late = models.BooleanField(default=False)
    is_early_departure = models.BooleanField(default=False)
    overtime_minutes = models.PositiveIntegerField(default=0)
    is_manually_corrected = models.BooleanField(default=False)

    class Meta:
        unique_together = [("employee", "date")]
        indexes = [
            models.Index(fields=["date", "status"]),
            models.Index(fields=["employee", "date"]),
        ]
        ordering = ["-date"]

    def __str__(self):
        return f"{self.employee.employee_code} — {self.date} ({self.status})"


class AttendanceCorrectionRequest(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    attendance = models.ForeignKey(
        Attendance, on_delete=models.CASCADE, related_name="correction_requests"
    )
    requested_by = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        related_name="corrections_requested",
    )
    reviewed_by = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="corrections_reviewed",
    )
    proposed_check_in = models.DateTimeField(null=True, blank=True)
    proposed_check_out = models.DateTimeField(null=True, blank=True)
    justification = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Correction for {self.attendance} ({self.status})"


class Camera(TimeStampedModel):
    class Status(models.TextChoices):
        ONLINE = "ONLINE", "Online"
        OFFLINE = "OFFLINE", "Offline"
        DEGRADED = "DEGRADED", "Degraded"

    class GateType(models.TextChoices):
        ENTRY = "ENTRY", "Entry"
        EXIT = "EXIT", "Exit"
        BOTH = "BOTH", "Both"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)  # e.g. "Gate 3 - Entry"
    department = models.ForeignKey(
        "locations.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="cameras"
    )
    # Credentials belong in a secrets manager / env var, not in this field in
    # production — this stores the connection URL with a placeholder auth
    # section, e.g. rtsp://{CAM3_USER}:{CAM3_PASS}@10.0.1.15:554/stream1
    rtsp_url = models.CharField(max_length=500)
    gate_type = models.CharField(max_length=10, choices=GateType.choices, default=GateType.BOTH)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OFFLINE)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    is_enabled = models.BooleanField(default=True, help_text="Disable to stop the capture worker without deleting config")

    class Meta:
        indexes = [models.Index(fields=["status"]), models.Index(fields=["is_enabled"])]

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


class CameraHealthLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    camera = models.ForeignKey(Camera, on_delete=models.CASCADE, related_name="health_logs")
    status = models.CharField(max_length=20, choices=Camera.Status.choices)
    detail = models.CharField(max_length=255, blank=True)  # e.g. "connection timeout after 3 retries"
    logged_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["camera", "logged_at"])]
        ordering = ["-logged_at"]

    def __str__(self):
        return f"{self.camera.name}: {self.status} @ {self.logged_at:%Y-%m-%d %H:%M}"
