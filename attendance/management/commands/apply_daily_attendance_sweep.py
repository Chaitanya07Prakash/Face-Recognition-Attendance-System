import datetime

from django.core.management.base import BaseCommand

from attendance.services import apply_leave_and_holidays_for_date
from employees.models import Employee


class Command(BaseCommand):
    help = (
        "Fills in ABSENT/ON_LEAVE/HOLIDAY attendance rows for employees who had no "
        "recognition event on the given date. Run once the business date has fully "
        "elapsed (e.g. via a nightly cron/Celery beat job) — running it mid-day would "
        "mark people ON_LEAVE or ABSENT before they've had a chance to check in."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date", type=str, default=None,
            help="YYYY-MM-DD; defaults to yesterday",
        )

    def handle(self, *args, **options):
        target_date = (
            datetime.date.fromisoformat(options["date"])
            if options["date"]
            else datetime.date.today() - datetime.timedelta(days=1)
        )
        created = apply_leave_and_holidays_for_date(
            target_date, Employee.objects.filter(status=Employee.Status.ACTIVE)
        )
        self.stdout.write(self.style.SUCCESS(f"Created {created} attendance row(s) for {target_date}"))
