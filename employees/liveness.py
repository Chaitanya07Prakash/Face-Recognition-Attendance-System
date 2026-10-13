"""
Anti-spoofing liveness checks, layered on top of face recognition.

A static photo held up to the camera will match a real person's encoding
just fine — recognition alone can't tell "this is Priya" from "this is a
photo of Priya". These heuristics require real, live facial movement:

- Blink detection: eye-aspect-ratio (EAR) dipping below a threshold and
  recovering, across a short sequence of frames. A printed photo or a
  static phone screen won't blink.
- Head movement: enough landmark displacement across frames to rule out a
  perfectly still photo held in front of the camera (a video replay could
  still pass this alone — combine with blink detection for real protection,
  and treat this as a deterrent, not a cryptographic guarantee).

This runs against a short buffer of consecutive frames from the same
camera, not a single frame — that's the point: liveness is a property of
a sequence, not a snapshot.
"""

import logging
import os
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

try:
    import mediapipe as mp
    from mediapipe.tasks.python import vision as mp_vision
    from mediapipe.tasks.python.core.base_options import BaseOptions

    MEDIAPIPE_AVAILABLE = True
except ImportError:  # pragma: no cover
    MEDIAPIPE_AVAILABLE = False

# mediapipe's Tasks API (current as of this mediapipe version) requires a
# downloadable .task model bundle rather than bundling the model in the
# package itself, unlike the older mp.solutions.face_mesh API. Download it
# once and point this at the file (or set the env var):
#   curl -L -o face_landmarker.task \
#     https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
FACE_LANDMARKER_MODEL_PATH = os.environ.get(
    "FACE_LANDMARKER_MODEL_PATH",
    os.path.join(os.path.dirname(__file__), "models", "face_landmarker.task"),
)

_landmarker = None  # lazy-initialized so importing this module never requires the model file


def _get_landmarker():
    global _landmarker
    if _landmarker is not None:
        return _landmarker

    if not MEDIAPIPE_AVAILABLE:
        raise RuntimeError("mediapipe is not installed in this environment.")

    if not os.path.exists(FACE_LANDMARKER_MODEL_PATH):
        raise RuntimeError(
            f"Face landmarker model not found at {FACE_LANDMARKER_MODEL_PATH}. "
            "Download it with:\n"
            "  curl -L -o " + FACE_LANDMARKER_MODEL_PATH + " \\\n"
            "    https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/1/face_landmarker.task\n"
            "or set FACE_LANDMARKER_MODEL_PATH to point at an existing copy."
        )

    options = mp_vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=FACE_LANDMARKER_MODEL_PATH),
        running_mode=mp_vision.RunningMode.IMAGE,
        num_faces=1,
    )
    _landmarker = mp_vision.FaceLandmarker.create_from_options(options)
    return _landmarker


# FaceLandmarker outputs the same 478-point mesh topology as the legacy
# mp.solutions.face_mesh (with refine_landmarks=True), so these indices for
# the eye contour and stable head-pose points are unchanged.
LEFT_EYE = [362, 385, 387, 263, 373, 380]
RIGHT_EYE = [33, 160, 158, 133, 153, 144]
KEY_HEAD_POINTS = [1, 152, 234, 454]  # nose tip, chin, left cheek, right cheek

EAR_BLINK_THRESHOLD = 0.21   # below this, eye is considered closed
EAR_OPEN_THRESHOLD = 0.25    # above this, eye is considered open again
MIN_HEAD_MOVEMENT_PX = 3.0   # average landmark displacement to count as "not frozen"


def _eye_aspect_ratio(landmarks, eye_indices, frame_w, frame_h):
    """
    landmarks: a sequence supporting landmarks[i].x / .y in normalized
    [0, 1] coordinates (matches both the mediapipe result type and simple
    mock objects used in tests).
    """
    pts = np.array([(landmarks[i].x * frame_w, landmarks[i].y * frame_h) for i in eye_indices])
    vertical_1 = np.linalg.norm(pts[1] - pts[5])
    vertical_2 = np.linalg.norm(pts[2] - pts[4])
    horizontal = np.linalg.norm(pts[0] - pts[3])
    if horizontal == 0:
        return 0.0
    return (vertical_1 + vertical_2) / (2.0 * horizontal)


def extract_landmarks(frame_rgb: np.ndarray):
    """Returns the landmark list for the first detected face, or None."""
    landmarker = _get_landmarker()
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
    result = landmarker.detect(mp_image)
    if not result.face_landmarks:
        return None
    return result.face_landmarks[0]


@dataclass
class LivenessTracker:
    """
    Stateful, per-camera-session tracker. Feed it consecutive frames via
    `update()`; call `is_live()` once enough frames have been buffered
    (recommend >= 15 frames, roughly half a second at 30fps).
    """

    ear_history: list = field(default_factory=list)
    landmark_history: list = field(default_factory=list)
    blink_detected: bool = False
    _eye_was_open: bool = True

    def update(self, frame_rgb: np.ndarray) -> bool:
        """
        Processes one frame. Returns False if no face was found in this
        frame (caller should skip it rather than count it toward the
        liveness window).
        """
        landmarks = extract_landmarks(frame_rgb)
        if landmarks is None:
            return False

        h, w = frame_rgb.shape[:2]
        self._ingest_landmarks(landmarks, w, h)
        return True

    def _ingest_landmarks(self, landmarks, w, h):
        """Split out from update() so tests can feed synthetic landmarks without mediapipe."""
        left_ear = _eye_aspect_ratio(landmarks, LEFT_EYE, w, h)
        right_ear = _eye_aspect_ratio(landmarks, RIGHT_EYE, w, h)
        avg_ear = (left_ear + right_ear) / 2.0
        self.ear_history.append(avg_ear)

        # Blink = a transition from open -> closed -> open across frames.
        if self._eye_was_open and avg_ear < EAR_BLINK_THRESHOLD:
            self._eye_was_open = False
        elif not self._eye_was_open and avg_ear > EAR_OPEN_THRESHOLD:
            self._eye_was_open = True
            self.blink_detected = True

        self.landmark_history.append(
            np.array([(landmarks[i].x * w, landmarks[i].y * h) for i in KEY_HEAD_POINTS])
        )

    def head_movement_score(self) -> float:
        if len(self.landmark_history) < 2:
            return 0.0
        displacements = [
            np.linalg.norm(self.landmark_history[i] - self.landmark_history[i - 1], axis=1).mean()
            for i in range(1, len(self.landmark_history))
        ]
        return float(np.mean(displacements))

    def is_live(self) -> bool:
        """
        A frame sequence passes liveness if it shows a genuine blink OR
        enough head movement across the buffered frames. Requiring only one
        of the two (not both) keeps false rejections low for someone who
        holds reasonably still but does blink during a ~1s window.
        """
        if len(self.ear_history) < 5:
            return False  # not enough data yet, caller should keep buffering
        return self.blink_detected or self.head_movement_score() > MIN_HEAD_MOVEMENT_PX
