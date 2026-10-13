"""
Per-camera capture worker. Run one of these as a separate OS process per
active camera (see run_camera_fleet.py for the multiprocessing pool that
launches one per Camera row).

Deliberately does the *minimum* CPU work here: grab a frame, throttle to a
low sample rate, do a cheap face-presence check, and hand anything
interesting off to Celery. The actual encoding/matching work in
attendance.tasks.process_frame runs in a separate worker pool, so a slow
recognition pass never causes this process to fall behind on the RTSP
stream and drop frames (or the connection).
"""

import datetime
import logging
import time

import cv2

logger = logging.getLogger(__name__)

SAMPLE_INTERVAL_SECONDS = 1.0 / 3  # ~3 fps sampling from the live stream
RECONNECT_BACKOFF_SECONDS = [2, 5, 10, 30, 60]  # capped exponential-ish backoff
MAX_CONSECUTIVE_FAILURES_BEFORE_DEGRADED = 5


def _open_stream(rtsp_url: str):
    cap = cv2.VideoCapture(rtsp_url)
    # Keep the internal buffer tiny so we're always looking at a recent
    # frame, not replaying a backlog if processing falls behind.
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def run_camera_worker(camera_id: str, rtsp_url: str, stop_event=None):
    """
    Blocking loop — intended to be the target of a multiprocessing.Process.
    `stop_event` is a multiprocessing.Event; when set, the loop exits
    cleanly on its next iteration.
    """
    # Imports deferred to inside the function: this runs in a forked child
    # process, and importing Django/Celery at module level would make every
    # camera process pay Django's app-registry setup cost even before it's
    # clear this worker will be launched via multiprocessing.
    import django

    django.setup()

    from attendance.tasks import encode_frame_for_task, process_frame, record_camera_heartbeat

    backoff_index = 0
    consecutive_failures = 0
    cap = None

    logger.info("Starting capture worker for camera %s", camera_id)

    while stop_event is None or not stop_event.is_set():
        if cap is None or not cap.isOpened():
            cap = _open_stream(rtsp_url)
            if not cap.isOpened():
                delay = RECONNECT_BACKOFF_SECONDS[min(backoff_index, len(RECONNECT_BACKOFF_SECONDS) - 1)]
                consecutive_failures += 1
                status = (
                    "DEGRADED" if consecutive_failures >= MAX_CONSECUTIVE_FAILURES_BEFORE_DEGRADED
                    else "OFFLINE"
                )
                record_camera_heartbeat.delay(
                    camera_id, status, f"connection failed, retrying in {delay}s (attempt {consecutive_failures})"
                )
                logger.warning("Camera %s: connect failed, retrying in %ss", camera_id, delay)
                time.sleep(delay)
                backoff_index += 1
                continue

            backoff_index = 0  # reset backoff on a successful (re)connect

        ok, frame = cap.read()
        if not ok:
            consecutive_failures += 1
            cap.release()
            cap = None
            record_camera_heartbeat.delay(
                camera_id, "OFFLINE", f"frame grab failed (failure #{consecutive_failures})"
            )
            logger.warning("Camera %s: frame grab failed, will reconnect", camera_id)
            continue

        consecutive_failures = 0
        record_camera_heartbeat.delay(camera_id, "ONLINE", "")

        # Cheap presence check before paying for JPEG encoding + a Celery
        # round-trip: Haar cascade is much cheaper than the HOG/CNN detector
        # used downstream, good enough to decide "is it worth dispatching".
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(60, 60))

        if len(faces) > 0:
            encoded = encode_frame_for_task(frame)
            captured_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            process_frame.delay(camera_id, encoded, captured_at)

        time.sleep(SAMPLE_INTERVAL_SECONDS)

    if cap is not None:
        cap.release()
    logger.info("Capture worker for camera %s stopped", camera_id)
