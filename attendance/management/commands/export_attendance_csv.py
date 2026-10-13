import csv
import datetime

from django.core.management.base import BaseCommand

from attendance.models import Attendance


class Command(BaseCommand):
    help = "Export attendance records to a CSV file"

    def add_arguments(self, parser):
        parser.add_argument("output_path", type=str)
        parser.add_argument("--date-from", type=str, help="YYYY-MM-DD")
        parser.add_argument("--date-to", type=str, help="YYYY-MM-DD")

    def handle(self, *args, **options):
        qs = Attendance.objects.select_related("employee", "employee__department").order_by("date")

        if options["date_from"]:
            qs = qs.filter(date__gte=datetime.date.fromisoformat(options["date_from"]))
        if options["date_to"]:
            qs = qs.filter(date__lte=datetime.date.fromisoformat(options["date_to"]))

        with open(options["output_path"], "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "employee_code", "full_name", "department", "date",
                "check_in_time", "check_in_confidence",
                "check_out_time", "check_out_confidence", "status",
            ])
            count = 0
            for record in qs.iterator():
                writer.writerow([
                    record.employee.employee_code,
                    record.employee.full_name,
                    record.employee.department.name,
                    record.date.isoformat(),
                    record.check_in_time.isoformat() if record.check_in_time else "",
                    record.check_in_confidence or "",
                    record.check_out_time.isoformat() if record.check_out_time else "",
                    record.check_out_confidence or "",
                    record.status,
                ])
                count += 1

        self.stdout.write(self.style.SUCCESS(f"Exported {count} record(s) to {options['output_path']}"))
