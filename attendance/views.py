import datetime
import time
from typing import Generator
import cv2
import numpy as np

from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone

from attendance.models import Attendance
from attendance.services import mark_attendance
from employees.models import Employee
from employees.recognition import load_known_encodings, recognize_encoding

try:
    import face_recognition
except ImportError:
    face_recognition = None


def open_camera(camera_index: int = 0):
    """Try DirectShow on Windows, fallback to default backend."""
    for backend in [cv2.CAP_DSHOW, cv2.CAP_ANY]:
        for idx in [camera_index, 0, 1, 2]:
            try:
                cap = cv2.VideoCapture(idx, backend)
                if cap.isOpened():
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        return cap, idx
                    cap.release()
            except Exception:
                pass
    return None, camera_index


def gen_attendance_frames() -> Generator[bytes, None, None]:
    """Yield MJPEG multipart stream with real-time recognition & attendance marking."""
    if face_recognition is None:
        err_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(err_frame, "face_recognition not installed", (40, 240),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        _, jpeg = cv2.imencode('.jpg', err_frame)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
        return

    cap, _ = open_camera(0)
    if cap is None or not cap.isOpened():
        err_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(err_frame, "Webcam not accessible.", (60, 220),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        cv2.putText(err_frame, "Ensure no other app is using camera.", (60, 260),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        _, jpeg = cv2.imencode('.jpg', err_frame)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
        return

    employee_ids, known_vectors = load_known_encodings()
    employee_cache = {}
    frame_count = 0
    last_results = []
    last_refresh = time.monotonic()

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            frame_count += 1
            if time.monotonic() - last_refresh > 60:
                employee_ids, known_vectors = load_known_encodings()
                last_refresh = time.monotonic()

            if frame_count % 3 == 0:
                small_frame = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                rgb_small = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
                face_locations = face_recognition.face_locations(rgb_small, model="hog")
                face_encodings = face_recognition.face_encodings(rgb_small, face_locations)

                last_results = []
                for location, encoding in zip(face_locations, face_encodings):
                    emp_id, confidence = recognize_encoding(encoding, employee_ids, known_vectors)
                    if emp_id is None:
                        label = "Unknown"
                        color = (0, 0, 255)
                    else:
                        if emp_id not in employee_cache:
                            try:
                                employee_cache[emp_id] = Employee.objects.get(id=emp_id)
                            except Employee.DoesNotExist:
                                employee_cache[emp_id] = None
                        employee = employee_cache[emp_id]
                        if employee:
                            res = mark_attendance(employee, confidence)
                            label = f"{employee.full_name} ({confidence:.2f}) [{res.action}]"
                            color = (0, 200, 0)
                        else:
                            label = "Unknown"
                            color = (0, 0, 255)

                    # Scale back up
                    top, right, bottom, left = [v * 2 for v in location]
                    last_results.append(((top, right, bottom, left), label, color))

            # Draw boxes
            for (top, right, bottom, left), label, color in last_results:
                cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                cv2.rectangle(frame, (left, max(top - 25, 0)), (left + tw + 10, top), color, cv2.FILLED)
                cv2.putText(frame, label, (left + 5, top - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

            ret, jpeg = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ret:
                continue

            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            time.sleep(0.03)
    finally:
        cap.release()


@login_required
def live_camera_view(request):
    """Live Attendance Recognition Camera Page."""
    today = timezone.localdate()
    today_attendances = Attendance.objects.filter(date=today).select_related("employee", "shift").order_by("-updated_at")[:20]
    return render(request, "attendance/live_camera.html", {
        "today_attendances": today_attendances,
        "today": today,
    })


@login_required
def video_feed_view(request):
    """Stream live webcam recognition feed."""
    return StreamingHttpResponse(
        gen_attendance_frames(),
        content_type='multipart/x-mixed-replace; boundary=frame'
    )


@login_required
def attendance_events_api(request):
    """API returning today's real-time attendance events."""
    today = timezone.localdate()
    records = Attendance.objects.filter(date=today).select_related("employee", "shift").order_by("-updated_at")[:15]
    data = []
    for r in records:
        data.append({
            "employee_code": r.employee.employee_code,
            "employee_name": r.employee.full_name,
            "check_in": r.check_in_time.strftime("%H:%M:%S") if r.check_in_time else "—",
            "check_out": r.check_out_time.strftime("%H:%M:%S") if r.check_out_time else "—",
            "status": r.get_status_display(),
            "is_late": r.is_late,
            "confidence": f"{r.check_in_confidence * 100:.0f}%" if r.check_in_confidence else "—",
        })
    return JsonResponse({"events": data})
