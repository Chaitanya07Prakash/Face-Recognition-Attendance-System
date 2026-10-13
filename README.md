# Face Recognition Attendance System — Phase 1 through 4 + Web UI

## Phase 1 — Foundations
Custom auth (`CustomUser` + role), RBAC via Django Groups, Area -> Project
-> Mine -> Department hierarchy, Employee CRUD.

## Phase 2 — Core recognition MVP
`FaceEncoding`, CV pipeline, `enroll_face` / `run_recognition` commands.

## Phase 3 — Shift-aware attendance
`Shift`, `EmployeeShiftAssignment`, `Holiday`, `LeaveRequest`,
`AttendanceCorrectionRequest`. Fixes the night-shift midnight bug.

## Phase 4 — Multi-camera scale, liveness, security
`Camera`/`CameraHealthLog`, Redis+Celery async pipeline, MediaPipe
liveness detection, TOTP 2FA, audit trail signals.

## Web UI (this update) — register and enroll employees from a browser

No more admin-only workflow. Log in at `/login/`, and:

- **`/employees/`** — searchable employee list (HTMX live search, no page
  reload), face-sample count per employee.
- **`/employees/new/`** — registration form with cascading Area -> Project
  -> Mine -> Department dropdowns (HTMX-powered; each selection narrows
  the next). Gated behind `employees.add_employee` permission — HR and
  Admin can register employees, Security cannot (confirmed by
  `test_security_role_cannot_access_registration_form`).
- **`/employees/<id>/enroll/`** — in-browser webcam capture. Click "Start
  camera", take 3+ snapshots (or fall back to uploading existing photo
  files if there's no webcam), then submit. This calls the same
  `enroll_employee_images()` used by the `enroll_face` CLI command
  underneath — no duplicate logic.
- **`/employees/<id>/`** — profile page: enrolled face samples with
  quality scores, and recent attendance history.

Registering an employee redirects straight into the enrollment page for
that employee, so the natural flow is: fill form -> submit -> camera
opens -> capture faces -> done.

### Login

Uses Django's built-in auth views. Any `CustomUser` you create via
`createsuperuser` or the shell can log in at `/login/`. Role-based access
is enforced the same way as the admin (via the RBAC groups from
`seed_roles`).

## Setup

    python3 -m venv venv
    source venv/bin/activate        # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    python manage.py migrate
    python manage.py seed_roles
    python manage.py createsuperuser
    python manage.py runserver

Then visit `http://127.0.0.1:8000/employees/` (redirects to `/login/` if
not authenticated) or `http://127.0.0.1:8000/admin/` for the raw admin.

**Windows-specific notes learned from real installs:**
- If `pip install dlib` fails to compile, install a prebuilt wheel instead,
  e.g. `pip install https://github.com/sachadee/Dlib/raw/main/dlib-20.0.0-cp313-cp313-win_amd64.whl`
  (match the `cp3XX` tag to your Python version) then `pip install face_recognition`.
- If `face_recognition` reports "Please install face_recognition_models"
  even though it's actually installed, that's a known bug: newer
  `setuptools` (81+) removed `pkg_resources`, which
  `face_recognition_models` relies on to check itself. Fix:
  `pip install "setuptools<81"`.
- Always double-check your venv is actually active (`(venv)` in the
  prompt) and that `where.exe python` / `which python` points inside
  `venv\` before installing anything — installing into the wrong global
  Python is the single most common source of "it says installed but the
  app still can't find it" confusion.

## Commands

    python manage.py enroll_face <employee_code> <image_dir>
    python manage.py run_recognition [--camera-index 0] [--display]
    python manage.py export_attendance_csv <output.csv> [--date-from] [--date-to]
    python manage.py apply_daily_attendance_sweep [--date YYYY-MM-DD]
    python manage.py run_camera_fleet

## Tests

    python manage.py test

53 tests across all phases plus the web UI (registration form validation,
permission enforcement, cascading dropdowns, HTMX partial responses,
enrollment upload flow) — all pass without dlib/face_recognition/mediapipe
installed, by design, matching the actual dev environment this was built
and tested in. The web UI was additionally verified against a real running
dev server with `curl` (login, CSRF, session cookies, redirects) — not
just the Django test client.

## Notes for Phase 5

- Real-time dashboard (Django Channels) can hook into the same
  `mark_attendance()` choke point already used everywhere else.
- Reports (PDF/Excel), notifications, Postgres+pgvector migration,
  deployment (Docker Compose), locking down `SECURE_*`/`DEBUG` settings.
- The employee list/detail templates are intentionally plain — a good
  place to add photo thumbnails, bulk CSV import, and pagination once
  employee counts grow past what fits on one page.
