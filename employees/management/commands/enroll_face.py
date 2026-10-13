from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from employees.models import Employee
from employees.recognition import enroll_employee_images

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


class Command(BaseCommand):
    help = "Enroll face samples for an employee from a directory of images"

    def add_arguments(self, parser):
        parser.add_argument("employee_code", type=str)
        parser.add_argument("image_dir", type=str, help="Folder containing 3+ sample photos")
        parser.add_argument("--min-quality", type=float, default=0.4)

    def handle(self, *args, **options):
        try:
            employee = Employee.objects.get(employee_code=options["employee_code"])
        except Employee.DoesNotExist:
            raise CommandError(f"No employee with code {options['employee_code']}")

        image_dir = Path(options["image_dir"])
        if not image_dir.is_dir():
            raise CommandError(f"{image_dir} is not a directory")

        image_paths = sorted(
            str(p) for p in image_dir.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS
        )
        if not image_paths:
            raise CommandError(f"No images found in {image_dir}")

        self.stdout.write(f"Enrolling {len(image_paths)} image(s) for {employee}...")
        created, rejected = enroll_employee_images(
            employee, image_paths, min_quality=options["min_quality"]
        )

        self.stdout.write(self.style.SUCCESS(f"Created {created} face encoding(s)"))
        for path, reason in rejected:
            self.stdout.write(self.style.WARNING(f"  rejected {path}: {reason}"))

        if created < 3:
            self.stdout.write(
                self.style.WARNING(
                    "Fewer than 3 usable samples enrolled — recognition accuracy will suffer. "
                    "Recapture with better lighting/angles."
                )
            )
