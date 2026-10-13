import uuid

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Employee(TimeStampedModel):
    """
    The HR/business profile. Deliberately separate from CustomUser: not
    every employee tracked by face recognition needs login access, and not
    every CustomUser (e.g. a Security Officer account) is an employee.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        SUSPENDED = "SUSPENDED", "Suspended"
        TERMINATED = "TERMINATED", "Terminated"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_code = models.CharField(max_length=30, unique=True, db_index=True)
    user_account = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_profile",
    )
    full_name = models.CharField(max_length=200)
    department = models.ForeignKey(
        "locations.Department", on_delete=models.PROTECT, related_name="employees"
    )
    designation = models.CharField(max_length=100)
    date_joined = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["department", "status"]),
            models.Index(fields=["full_name"]),
        ]

    def __str__(self):
        return f"{self.employee_code} — {self.full_name}"


class FaceEncoding(TimeStampedModel):
    """
    One row per enrolled sample image (multi-image enrollment). Only the
    derived 128-d embedding is stored — never the raw face image — per the
    security requirement that facial data is stored as vectorized data.

    `vector` is a JSONField (list[float]) on SQLite for local dev. When the
    project migrates to Postgres in Phase 4/5, swap this for
    pgvector.django.VectorField(dimensions=128) and write a data migration
    to copy the JSON lists across — callers already work with plain Python
    lists via `.vector`, so no application code changes needed beyond the
    field type.
    """

    class Source(models.TextChoices):
        ENROLLMENT = "ENROLLMENT", "Enrollment"
        RECAPTURE = "RECAPTURE", "Recapture"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="face_encodings"
    )
    vector = models.JSONField(help_text="128-d face encoding as a list of floats")
    quality_score = models.FloatField(
        help_text="0.0-1.0 heuristic score from enrollment-time quality checks "
        "(sharpness, face size, single-face-only)"
    )
    capture_source = models.CharField(
        max_length=20, choices=Source.choices, default=Source.ENROLLMENT
    )
    is_active = models.BooleanField(
        default=True, help_text="Soft-disable a bad sample without losing history"
    )

    class Meta:
        indexes = [models.Index(fields=["employee", "is_active"])]

    def __str__(self):
        return f"Encoding for {self.employee.employee_code} ({self.capture_source})"
