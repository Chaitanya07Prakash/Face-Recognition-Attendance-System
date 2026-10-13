"""
Business logic for turning recognition events into Attendance rows, and for
the manual correction and leave/holiday workflows around them.
"""

import datetime
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from attendance.models import (
    Attendance,
    AttendanceCorrectionRequest,
    EmployeeShiftAssignment,
    Holiday,
    LeaveRequest,
)

# How close together two recognition events for the same employee on the
# same shift must be to be treated as the *same* physical visit (i.e. the
# person lingering in front of the camera) rather than a genuine check-out.
DUPLICATE_WINDOW_MINUTES = 5


@dataclass
class AttendanceResult:
    action: str  # "check_in" | "duplicate_ignored" | "check_out"
    record: Attendance


def get_active_shift(employee, on_date: datetime.date):
    """
    Returns the EmployeeShiftAssignment.shift in effect for `employee` on
    `on_date`, or None if unassigned. If multiple assignments overlap
    (shouldn't happen with correct data entry, but don't crash on it),
    the most recently created one wins.
    """
    assignment = (
        EmployeeShiftAssignment.objects.filter(
            employee=employee, effective_from__lte=on_date
        )
        .filter(models_q_effective_to_ok(on_date))
        .order_by("-effective_from", "-created_at")
        .select_related("shift")
        .first()
    )
    return assignment.shift if assignment else None


def models_q_effective_to_ok(on_date):
    from django.db.models import Q

    return Q(effective_to__isnull=True) | Q(effective_to__gte=on_date)


def resolve_business_date(employee, event_time: datetime.datetime) -> tuple[datetime.date, "Shift | None"]:
    """
    Determines which attendance "day" an event belongs to, accounting for
    night shifts crossing midnight. This is the fix for the Phase 2 bug
    where an 8pm-to-4am shift's check-in and check-out landed on two
    different calendar dates and created two Attendance rows instead of one.

    Logic: look up the shift assigned as of event_time's calendar date. If
    it's a night shift, and event_time's wall-clock time is *before* that
    day's scheduled start (meaning this is an early-morning event still
    belonging to the shift that started the previous evening), attribute
    it to the previous calendar date instead.
    """
    calendar_date = event_time.date()
    shift = get_active_shift(employee, calendar_date)

    if shift is None or not shift.is_night_shift:
        return calendar_date, shift

    todays_shift_start = timezone.make_aware(
        datetime.datetime.combine(calendar_date, shift.start_time),
        timezone.get_current_timezone(),
    ) if timezone.is_naive(event_time) else datetime.datetime.combine(
        calendar_date, shift.start_time, tzinfo=event_time.tzinfo
    )

    if event_time < todays_shift_start:
        # Early-morning event: still part of the shift that started yesterday.
        business_date = calendar_date - datetime.timedelta(days=1)
        shift = get_active_shift(employee, business_date) or shift
        return business_date, shift

    return calendar_date, shift


def _shift_datetime(business_date: datetime.date, t: datetime.time, tzinfo, crosses_midnight=False):
    d = business_date + datetime.timedelta(days=1) if crosses_midnight else business_date
    return datetime.datetime.combine(d, t, tzinfo=tzinfo)


def _apply_late_early_overtime(record: Attendance, shift):
    """Mutates record's is_late / is_early_departure / overtime_minutes in place. Does not save()."""
    if shift is None:
        return

    tzinfo = record.check_in_time.tzinfo if record.check_in_time else timezone.get_current_timezone()
    scheduled_start = _shift_datetime(record.date, shift.start_time, tzinfo)
    scheduled_end = _shift_datetime(
        record.date, shift.end_time, tzinfo, crosses_midnight=shift.is_night_shift
    )

    if record.check_in_time:
        grace_deadline = scheduled_start + datetime.timedelta(minutes=shift.grace_minutes)
        record.is_late = record.check_in_time > grace_deadline

    if record.check_out_time:
        record.is_early_departure = record.check_out_time < scheduled_end
        worked_minutes = (record.check_out_time - (record.check_in_time or scheduled_start)).total_seconds() / 60
        overtime = worked_minutes - shift.scheduled_minutes()
        record.overtime_minutes = max(0, round(overtime))


@transaction.atomic
def mark_attendance(employee, confidence: float, event_time: datetime.datetime | None = None) -> AttendanceResult:
    """
    Idempotent entry point called by the recognition loop every time a face
    is matched. Enforces duplicate-attendance prevention and is shift-aware:

    - Business date is resolved via resolve_business_date (handles night
      shifts crossing midnight correctly — see that function's docstring).
    - First recognition for the business date -> creates the row, check-in.
    - A second recognition within DUPLICATE_WINDOW_MINUTES of the last
      event -> ignored, doesn't touch the row.
    - A later recognition -> treated as check-out; late/early/overtime are
      recalculated against the assigned shift.
    """
    event_time = event_time or timezone.now()
    business_date, shift = resolve_business_date(employee, event_time)

    record, created = Attendance.objects.get_or_create(
        employee=employee,
        date=business_date,
        defaults={
            "shift": shift,
            "check_in_time": event_time,
            "check_in_confidence": confidence,
            "status": Attendance.Status.PRESENT,
        },
    )
    if created:
        _apply_late_early_overtime(record, shift)
        record.save(update_fields=["is_late", "is_early_departure", "overtime_minutes"])
        return AttendanceResult(action="check_in", record=record)

    reference_time = record.check_out_time or record.check_in_time
    if reference_time and (event_time - reference_time) < datetime.timedelta(
        minutes=DUPLICATE_WINDOW_MINUTES
    ):
        return AttendanceResult(action="duplicate_ignored", record=record)

    record.check_out_time = event_time
    record.check_out_confidence = confidence
    _apply_late_early_overtime(record, shift or record.shift)
    record.save(
        update_fields=[
            "check_out_time", "check_out_confidence",
            "is_early_departure", "overtime_minutes", "updated_at",
        ]
    )
    return AttendanceResult(action="check_out", record=record)


# ---------------------------------------------------------------------------
# Leave & holiday application
# ---------------------------------------------------------------------------

def apply_leave_and_holidays_for_date(business_date: datetime.date, employees_queryset):
    """
    For employees with no attendance row yet on business_date, create one
    marked ON_LEAVE or HOLIDAY where applicable. Meant to be run once the
    business date has fully elapsed (see the generate_daily_attendance
    management command) — running it mid-day would prematurely mark someone
    ON_LEAVE before they've had a chance to check in.
    """
    created = 0
    for employee in employees_queryset:
        if Attendance.objects.filter(employee=employee, date=business_date).exists():
            continue

        area = employee.department.mine.project.area

        is_holiday = Holiday.objects.filter(date=business_date, area__isnull=True).exists() or \
            Holiday.objects.filter(date=business_date, area=area).exists()

        approved_leave = LeaveRequest.objects.filter(
            employee=employee,
            status=LeaveRequest.Status.APPROVED,
            start_date__lte=business_date,
            end_date__gte=business_date,
        ).exists()

        if is_holiday:
            status = Attendance.Status.HOLIDAY
        elif approved_leave:
            status = Attendance.Status.ON_LEAVE
        else:
            status = Attendance.Status.ABSENT

        Attendance.objects.create(employee=employee, date=business_date, status=status)
        created += 1

    return created


# ---------------------------------------------------------------------------
# Manual correction workflow (HR/manager approval)
# ---------------------------------------------------------------------------

def request_correction(attendance: Attendance, requested_by, justification: str,
                        proposed_check_in=None, proposed_check_out=None) -> AttendanceCorrectionRequest:
    return AttendanceCorrectionRequest.objects.create(
        attendance=attendance,
        requested_by=requested_by,
        justification=justification,
        proposed_check_in=proposed_check_in,
        proposed_check_out=proposed_check_out,
    )


@transaction.atomic
def review_correction(correction: AttendanceCorrectionRequest, reviewed_by, approve: bool) -> AttendanceCorrectionRequest:
    if correction.status != AttendanceCorrectionRequest.Status.PENDING:
        raise ValueError("Only pending corrections can be reviewed")

    correction.reviewed_by = reviewed_by
    correction.reviewed_at = timezone.now()
    correction.status = (
        AttendanceCorrectionRequest.Status.APPROVED
        if approve
        else AttendanceCorrectionRequest.Status.REJECTED
    )
    correction.save(update_fields=["reviewed_by", "reviewed_at", "status"])

    if approve:
        record = correction.attendance
        if correction.proposed_check_in is not None:
            record.check_in_time = correction.proposed_check_in
        if correction.proposed_check_out is not None:
            record.check_out_time = correction.proposed_check_out
        record.is_manually_corrected = True
        _apply_late_early_overtime(record, record.shift)
        record.save()

    return correction
