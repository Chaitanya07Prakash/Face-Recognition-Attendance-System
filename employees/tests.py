import numpy as np
from django.test import TestCase

from employees.recognition import compute_quality_score


class QualityScoreTests(TestCase):
    def test_sharp_high_contrast_image_scores_higher_than_blurred(self):
        rng = np.random.default_rng(42)
        sharp = rng.integers(0, 255, size=(300, 300, 3), dtype=np.uint8)

        # A flat gray image has ~zero Laplacian variance -> low sharpness score.
        blurred = np.full((300, 300, 3), 128, dtype=np.uint8)

        sharp_score = compute_quality_score(sharp)
        blurred_score = compute_quality_score(blurred)

        self.assertGreater(sharp_score, blurred_score)

    def test_score_is_bounded_between_0_and_1(self):
        rng = np.random.default_rng(7)
        image = rng.integers(0, 255, size=(200, 200, 3), dtype=np.uint8)
        score = compute_quality_score(image)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_larger_face_box_scores_higher_than_tiny_face_box(self):
        rng = np.random.default_rng(1)
        image = rng.integers(0, 255, size=(400, 400, 3), dtype=np.uint8)

        large_face = (50, 350, 350, 50)  # top, right, bottom, left — fills most of frame
        tiny_face = (190, 210, 210, 190)  # small box in the center

        large_score = compute_quality_score(image, face_box=large_face)
        tiny_score = compute_quality_score(image, face_box=tiny_face)

        self.assertGreater(large_score, tiny_score)


class MockLandmark:
    def __init__(self, x, y):
        self.x, self.y = x, y


def _make_eye_landmarks(indices, points):
    return {i: MockLandmark(*p) for i, p in zip(indices, points)}


class EyeAspectRatioTests(TestCase):
    """
    Pure-math tests for the liveness EAR calculation — no mediapipe model
    file required, since these feed synthetic landmark objects directly.
    """

    def test_open_eye_has_higher_ear_than_closed_eye(self):
        from employees.liveness import LEFT_EYE, _eye_aspect_ratio

        open_pts = [(0.40, 0.50), (0.42, 0.47), (0.46, 0.47), (0.48, 0.50), (0.46, 0.53), (0.42, 0.53)]
        closed_pts = [(0.40, 0.50), (0.42, 0.495), (0.46, 0.495), (0.48, 0.50), (0.46, 0.505), (0.42, 0.505)]

        ear_open = _eye_aspect_ratio(_make_eye_landmarks(LEFT_EYE, open_pts), LEFT_EYE, 300, 300)
        ear_closed = _eye_aspect_ratio(_make_eye_landmarks(LEFT_EYE, closed_pts), LEFT_EYE, 300, 300)

        self.assertGreater(ear_open, ear_closed)


class LivenessTrackerTests(TestCase):
    """
    Tests the blink/head-movement state machine directly via
    _ingest_landmarks, bypassing mediapipe's frame detection (which needs
    the downloadable .task model file — see FACE_LANDMARKER_MODEL_PATH).
    """

    def test_blink_sequence_is_detected(self):
        from employees.liveness import KEY_HEAD_POINTS, LEFT_EYE, RIGHT_EYE, LivenessTracker

        open_pts = [(0.40, 0.50), (0.42, 0.47), (0.46, 0.47), (0.48, 0.50), (0.46, 0.53), (0.42, 0.53)]
        closed_pts = [(0.40, 0.50), (0.42, 0.495), (0.46, 0.495), (0.48, 0.50), (0.46, 0.505), (0.42, 0.505)]
        head_pts = [(0.5, 0.5)] * len(KEY_HEAD_POINTS)  # perfectly still head

        def frame_landmarks(eye_pts):
            landmarks = {}
            landmarks.update(_make_eye_landmarks(LEFT_EYE, eye_pts))
            landmarks.update(_make_eye_landmarks(RIGHT_EYE, eye_pts))
            landmarks.update(_make_eye_landmarks(KEY_HEAD_POINTS, head_pts))
            return landmarks

        tracker = LivenessTracker()
        # open -> closed -> open, i.e. a blink, with a still head throughout.
        for eye_pts in [open_pts, open_pts, closed_pts, closed_pts, open_pts, open_pts]:
            tracker._ingest_landmarks(frame_landmarks(eye_pts), 300, 300)

        self.assertTrue(tracker.blink_detected)
        self.assertTrue(tracker.is_live())  # blink alone is sufficient

    def test_frozen_photo_with_no_blink_and_no_movement_fails_liveness(self):
        from employees.liveness import KEY_HEAD_POINTS, LEFT_EYE, RIGHT_EYE, LivenessTracker

        open_pts = [(0.40, 0.50), (0.42, 0.47), (0.46, 0.47), (0.48, 0.50), (0.46, 0.53), (0.42, 0.53)]
        head_pts = [(0.5, 0.5)] * len(KEY_HEAD_POINTS)

        landmarks = {}
        landmarks.update(_make_eye_landmarks(LEFT_EYE, open_pts))
        landmarks.update(_make_eye_landmarks(RIGHT_EYE, open_pts))
        landmarks.update(_make_eye_landmarks(KEY_HEAD_POINTS, head_pts))

        tracker = LivenessTracker()
        for _ in range(10):  # identical frame every time — a static photo
            tracker._ingest_landmarks(landmarks, 300, 300)

        self.assertFalse(tracker.blink_detected)
        self.assertEqual(tracker.head_movement_score(), 0.0)
        self.assertFalse(tracker.is_live())

    def test_head_movement_alone_passes_liveness_without_a_blink(self):
        from employees.liveness import KEY_HEAD_POINTS, LEFT_EYE, RIGHT_EYE, LivenessTracker

        open_pts = [(0.40, 0.50), (0.42, 0.47), (0.46, 0.47), (0.48, 0.50), (0.46, 0.53), (0.42, 0.53)]

        tracker = LivenessTracker()
        for step in range(6):
            # Head drifts noticeably across frames; eyes stay open throughout.
            head_pts = [(0.5 + step * 0.02, 0.5 + step * 0.02)] * len(KEY_HEAD_POINTS)
            landmarks = {}
            landmarks.update(_make_eye_landmarks(LEFT_EYE, open_pts))
            landmarks.update(_make_eye_landmarks(RIGHT_EYE, open_pts))
            landmarks.update(_make_eye_landmarks(KEY_HEAD_POINTS, head_pts))
            tracker._ingest_landmarks(landmarks, 300, 300)

        self.assertFalse(tracker.blink_detected)
        self.assertGreater(tracker.head_movement_score(), 0)
        self.assertTrue(tracker.is_live())

    def test_extract_landmarks_raises_clear_error_without_model_file(self):
        from employees.liveness import extract_landmarks
        import numpy as np

        frame = np.zeros((100, 100, 3), dtype="uint8")
        with self.assertRaisesMessage(RuntimeError, "model not found"):
            extract_landmarks(frame)
