import time

import cv2

from django.core.management.base import BaseCommand

from employees.models import Employee
from employees.recognition import load_known_encodings, recognize_encoding
from attendance.services import mark_attendance

try:
    import face_recognition
except ImportError:
    face_recognition = None

# Reload known encodings from the DB this often, so newly enrolled
# employees show up without restarting the process.
ENCODING_REFRESH_SECONDS = 60


class Command(BaseCommand):
    help = "Run live face recognition against a webcam and log attendance (Phase 2: single camera, synchronous)"

    def add_arguments(self, parser):
        parser.add_argument("--camera-index", type=int, default=0)
        parser.add_argument("--display", action="store_true", help="Show the annotated video feed")

    def handle(self, *args, **options):
        if face_recognition is None:
            self.stderr.write(
                self.style.ERROR(
                    "face_recognition/dlib is not installed. Install it to run live recognition."
                )
            )
            return

        employee_ids, known_vectors = load_known_encodings()
        self.stdout.write(f"Loaded {len(employee_ids)} known face encoding(s).")
        employee_cache = {}
        last_refresh = time.monotonic()

        video = cv2.VideoCapture(options["camera_index"], cv2.CAP_DSHOW)
        if not video.isOpened():
            video = cv2.VideoCapture(options["camera_index"])
        if not video.isOpened():
            self.stderr.write(self.style.ERROR(f"Could not open camera index {options['camera_index']}"))
            return

        self.stdout.write("Starting recognition loop. Press Ctrl+C to stop.")
        try:
            while True:
                ok, frame = video.read()
                if not ok:
                    self.stderr.write(self.style.WARNING("Frame grab failed, retrying..."))
                    continue

                if time.monotonic() - last_refresh > ENCODING_REFRESH_SECONDS:
                    employee_ids, known_vectors = load_known_encodings()
                    last_refresh = time.monotonic()

                # Downscale for speed; face_recognition works fine on smaller frames.
                small_frame = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                rgb_small = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

                face_locations = face_recognition.face_locations(rgb_small, model="hog")
                face_encodings = face_recognition.face_encodings(rgb_small, face_locations)

                for location, encoding in zip(face_locations, face_encodings):
                    employee_id, confidence = recognize_encoding(encoding, employee_ids, known_vectors)

                    if employee_id is None:
                        label = "Unknown"
                    else:
                        if employee_id not in employee_cache:
                            employee_cache[employee_id] = Employee.objects.get(id=employee_id)
                        employee = employee_cache[employee_id]
                        result = mark_attendance(employee, confidence)
                        label = f"{employee.full_name} ({confidence:.2f}) [{result.action}]"
                        if result.action != "duplicate_ignored":
                            self.stdout.write(self.style.SUCCESS(f"{result.action}: {label}"))

                    if options["display"]:
                        top, right, bottom, left = [v * 2 for v in location]  # scale back up
                        cv2.rectangle(frame, (left, top), (right, bottom), (0, 200, 0), 2)
                        cv2.putText(
                            frame, label, (left, top - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 0), 2,
                        )

                if options["display"]:
                    cv2.imshow("Attendance recognition (q to quit)", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        except KeyboardInterrupt:
            pass
        finally:
            video.release()
            if options["display"]:
                cv2.destroyAllWindows()
            self.stdout.write("Recognition loop stopped.")
