"""
Async recognition pipeline. The camera capture worker (camera_worker.py)
does the lightweight job of grabbing frames and detecting "is there a face
here at all"; the actual encoding + matching + attendance write happens
here, off the capture loop, so a slow recognition pass never causes the
capture worker to drop frames or lose its RTSP connection.
"""

import base64
import logging

import cv2
import numpy as np
from celery import shared_task

logger = logging.getLogger(__name__)


def encode_frame_for_task(frame_bgr: np.ndarray) -> str:
    """JPEG-encodes and base64-encodes a frame for passing through the Celery broker."""
    ok, buffer = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError("Failed to JPEG-encode frame")
    return base64.b64encode(buffer).decode("ascii")


def decode_frame_from_task(encoded: str) -> np.ndarray:
    data = base64.b64decode(encoded)
    array = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(array, cv2.IMREAD_COLOR)


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    # If recognition genuinely errors out (not "no match found", but an
    # exception), don't silently drop the event — retry a couple of times
    # in case it's a transient DB connection blip, then give up loudly.
)
def process_frame(self, camera_id: str, encoded_frame: str, captured_at_iso: str):
    """
    Runs the full recognition + liveness + attendance pipeline for one
    frame, dispatched by a camera capture worker. Kept as a single task
    (rather than a chain) because encoding, liveness, and matching all need
    the same decoded frame and there's no benefit to splitting them across
    separate queue round-trips for one image.
    """
    import datetime

    from django.utils.dateparse import parse_datetime

    from attendance.models import Camera
    from attendance.services import mark_attendance
    from employees.models import Employee
    from employees.recognition import load_known_encodings, recognize_encoding

    try:
        import face_recognition
    except ImportError:
        logger.error("face_recognition not installed; cannot process frame")
        return {"status": "error", "reason": "face_recognition not installed"}

    try:
        frame_bgr = decode_frame_from_task(encoded_frame)
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

        face_locations = face_recognition.face_locations(frame_rgb, model="hog")
        if not face_locations:
            return {"status": "no_face"}

        encodings = face_recognition.face_encodings(frame_rgb, face_locations)
        employee_ids, known_vectors = load_known_encodings()

        results = []
        captured_at = parse_datetime(captured_at_iso) or datetime.datetime.now(datetime.timezone.utc)

        for encoding in encodings:
            employee_id, confidence = recognize_encoding(encoding, employee_ids, known_vectors)
            if employee_id is None:
                results.append({"status": "unknown_face"})
                continue

            employee = Employee.objects.get(id=employee_id)
            attendance_result = mark_attendance(employee, confidence, event_time=captured_at)
            results.append({
                "status": "matched",
                "employee_code": employee.employee_code,
                "confidence": confidence,
                "action": attendance_result.action,
            })

        return {"status": "ok", "camera_id": camera_id, "results": results}

    except Exception as exc:
        logger.exception("process_frame failed for camera %s", camera_id)
        raise self.retry(exc=exc)


@shared_task
def record_camera_heartbeat(camera_id: str, status: str, detail: str = ""):
    """
    Lightweight task the capture worker fires on every successful frame
    grab (or failure) so camera health lives in the DB without the capture
    process needing a direct, potentially-blocking DB connection of its own.
    """
    from django.utils import timezone

    from attendance.models import Camera, CameraHealthLog

    updated = Camera.objects.filter(id=camera_id).update(
        status=status,
        last_heartbeat=timezone.now(),
        consecutive_failures=0 if status == Camera.Status.ONLINE else models_f_increment(),
    )
    CameraHealthLog.objects.create(camera_id=camera_id, status=status, detail=detail)
    return {"updated": bool(updated)}


def models_f_increment():
    from django.db.models import F

    return F("consecutive_failures") + 1
