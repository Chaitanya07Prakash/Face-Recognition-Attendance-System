"""
Computer vision pipeline: enrollment-time encoding and live matching.

`compute_quality_score` only needs OpenCV and is unit-testable without
`face_recognition`/`dlib` installed. The enrollment and matching functions
require the full `face_recognition` stack (dlib's face detector + the
128-d ResNet encoder) — install per your existing working setup:

    pip install cmake dlib face_recognition opencv-python

On Windows, prefer a prebuilt dlib wheel over compiling from source if you
hit build-tool errors (matches the fix already used in the standalone
attendance project).
"""

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)

try:
    import face_recognition

    FACE_RECOGNITION_AVAILABLE = True
except ImportError:  # pragma: no cover
    FACE_RECOGNITION_AVAILABLE = False

MATCH_TOLERANCE = 0.5  # face_recognition distance threshold; lower = stricter
MIN_QUALITY_SCORE = 0.4


def compute_quality_score(image_bgr: np.ndarray, face_box: tuple[int, int, int, int] | None = None) -> float:
    """
    Cheap heuristic quality score in [0, 1], combining:
    - sharpness (variance of Laplacian, normalized)
    - face size relative to frame (bigger = more reliable encoding)

    face_box is (top, right, bottom, left) as returned by
    face_recognition.face_locations, or None to score the whole frame.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
    sharpness_score = min(sharpness / 500.0, 1.0)  # 500 is an empirically fine ceiling

    if face_box is not None:
        top, right, bottom, left = face_box
        face_area = max(0, (bottom - top)) * max(0, (right - left))
        frame_area = image_bgr.shape[0] * image_bgr.shape[1]
        size_score = min((face_area / frame_area) * 10, 1.0)  # face filling ~10% of frame -> 1.0
    else:
        size_score = 1.0

    return round(0.6 * sharpness_score + 0.4 * size_score, 3)


def _require_face_recognition():
    if not FACE_RECOGNITION_AVAILABLE:
        raise RuntimeError(
            "face_recognition/dlib is not installed in this environment. "
            "Install it to use enrollment or live recognition."
        )


def encode_single_face_image(image_path: str) -> tuple[list[float], float]:
    """
    Loads an image, requires exactly one face, and returns
    (encoding_as_list, quality_score). Raises ValueError if zero or
    multiple faces are found — enrollment images must be single-subject.
    """
    _require_face_recognition()

    image_rgb = face_recognition.load_image_file(image_path)
    face_locations = face_recognition.face_locations(image_rgb, model="hog")

    if len(face_locations) == 0:
        raise ValueError(f"No face detected in {image_path}")
    if len(face_locations) > 1:
        raise ValueError(
            f"Multiple faces ({len(face_locations)}) detected in {image_path}; "
            "enrollment images must contain exactly one person"
        )

    encodings = face_recognition.face_encodings(image_rgb, known_face_locations=face_locations)
    encoding = encodings[0]

    image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    quality = compute_quality_score(image_bgr, face_box=face_locations[0])

    return encoding.tolist(), quality


def enroll_employee_images(employee, image_paths: list[str], min_quality: float = MIN_QUALITY_SCORE):
    """
    Encodes each provided image and creates a FaceEncoding row per sample
    that clears the quality bar. Returns (created_count, rejected: list[(path, reason)]).
    Requires the caller to have already validated employee_code -> Employee.
    """
    from employees.models import FaceEncoding  # local import: keeps this module importable without Django apps loaded in ad-hoc scripts

    created = 0
    rejected = []

    for path in image_paths:
        try:
            vector, quality = encode_single_face_image(path)
        except ValueError as exc:
            rejected.append((path, str(exc)))
            continue

        if quality < min_quality:
            rejected.append((path, f"quality {quality} below minimum {min_quality}"))
            continue

        FaceEncoding.objects.create(
            employee=employee,
            vector=vector,
            quality_score=quality,
            capture_source=FaceEncoding.Source.ENROLLMENT,
        )
        created += 1

    if created == 0:
        logger.warning("No usable samples enrolled for %s", employee.employee_code)

    return created, rejected


def load_known_encodings():
    """
    Loads all active encodings for all employees into memory. Called once
    per recognition session (or periodically refreshed) rather than per
    frame — reloading per frame would hit the DB dozens of times a second.

    Returns (employee_ids: list[UUID], vectors: np.ndarray of shape (N, 128))
    """
    from employees.models import FaceEncoding

    rows = FaceEncoding.objects.filter(is_active=True).select_related("employee")
    employee_ids = [row.employee_id for row in rows]
    vectors = np.array([row.vector for row in rows]) if rows else np.empty((0, 128))
    return employee_ids, vectors


def recognize_encoding(unknown_encoding: np.ndarray, employee_ids: list, known_vectors: np.ndarray):
    """
    Compares one live face encoding against all known vectors. Returns
    (employee_id, confidence) for the best match under MATCH_TOLERANCE, or
    (None, 0.0) for an unknown/unmatched face.
    """
    _require_face_recognition()

    if known_vectors.shape[0] == 0:
        return None, 0.0

    distances = face_recognition.face_distance(known_vectors, unknown_encoding)
    best_index = int(np.argmin(distances))
    best_distance = float(distances[best_index])

    if best_distance > MATCH_TOLERANCE:
        return None, 0.0

    confidence = round(1.0 - best_distance, 3)
    return employee_ids[best_index], confidence
