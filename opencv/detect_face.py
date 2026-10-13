"""
detect_face.py
---------------
Loads previously registered face encodings (from `trained_faces.pkl`) and
runs real-time face detection + recognition on the webcam feed. Detected
faces are matched against known encodings; matches are labeled with the
person's name, and unmatched faces are labeled "Unknown".

Usage:
    python detect_face.py
    python detect_face.py --camera 0

Controls:
    'q' -> quit

Design notes:
    - Frames are downscaled before detection/encoding to keep the pipeline
      real-time; bounding boxes are scaled back up before drawing on the
      full-resolution frame.
    - Face matching uses `face_recognition.compare_faces` (Euclidean
      distance under the hood) with a configurable tolerance, and picks the
      closest match via `face_distance` to disambiguate when multiple
      registered faces pass the tolerance threshold.
    - Detection is only run every other frame (configurable) to further
      reduce CPU load, since face movement between consecutive frames is
      minor at typical webcam frame rates.
    - Gracefully runs even if no faces are registered yet, detecting faces
      and labeling them "Unknown" instead of crashing.
"""

import argparse
import os
import pickle
import sys
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import cv2
import face_recognition
import numpy as np

# ---------------------------------------------------------------------------
# Configuration & Path Resolution
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

def get_data_file() -> Path:
    """Find trained_faces.pkl across common project directories."""
    candidates = [
        DATA_DIR / "trained_faces.pkl",
        Path(__file__).resolve().parent / "trained_faces.pkl",
        BASE_DIR / "trained_faces.pkl",
    ]
    for p in candidates:
        if p.exists():
            return p
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / "trained_faces.pkl"

DATA_FILE = get_data_file()
FRAME_RESIZE_SCALE = 0.25       # downscale factor for detection speed
MATCH_TOLERANCE = 0.5           # lower = stricter match (default lib value is 0.6)
PROCESS_EVERY_N_FRAMES = 2      # skip frames to save CPU; 1 = process every frame
BOX_COLOR_KNOWN = (0, 200, 0)   # BGR green
BOX_COLOR_UNKNOWN = (0, 0, 255) # BGR red
WINDOW_NAME = "Face Recognition - press 'q' to quit"


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
def load_known_faces(data_file: Optional[Path] = None) -> Tuple[List[np.ndarray], List[str]]:
    """
    Load registered encodings from disk and split them into two parallel
    lists (encodings, names) for efficient batch comparison.
    If no registered faces exist yet, returns empty lists so the webcam
    can still detect faces as 'Unknown'.
    """
    target = data_file or get_data_file()
    if not target.exists():
        print(f"[WARN] No registered faces file found at '{target}'.")
        print("[INFO] Starting webcam in detection mode. All faces will be labeled 'Unknown'.")
        print("[INFO] Run 'python opencv/register_face.py' to register your name.")
        return [], []

    try:
        with open(target, "rb") as f:
            records: List[Dict] = pickle.load(f)
    except Exception as exc:
        print(f"[WARN] Could not load faces from '{target}': {exc}")
        return [], []

    if not records:
        print("[WARN] Face database is empty. Detecting faces as 'Unknown'.")
        return [], []

    encodings = [r["encoding"] for r in records if "encoding" in r]
    names = [r["name"] for r in records if "name" in r]
    return encodings, names


# ---------------------------------------------------------------------------
# Camera helper with Windows DirectShow support
# ---------------------------------------------------------------------------
def open_camera(preferred_index: int = 0) -> Tuple[Optional[cv2.VideoCapture], int]:
    """
    Attempts to open the webcam on Windows.
    Tries DirectShow (cv2.CAP_DSHOW) first for fast, reliable initialization,
    then falls back to default backend, and scans indices 0, 1, 2.
    """
    indices_to_try = [preferred_index]
    for i in [0, 1, 2]:
        if i not in indices_to_try:
            indices_to_try.append(i)

    # Try DirectShow first on Windows, then default
    for backend in [cv2.CAP_DSHOW, cv2.CAP_ANY]:
        for idx in indices_to_try:
            try:
                cap = cv2.VideoCapture(idx, backend)
                if cap.isOpened():
                    ret, test_frame = cap.read()
                    if ret and test_frame is not None:
                        return cap, idx
                    cap.release()
            except Exception:
                pass

    return None, preferred_index


# ---------------------------------------------------------------------------
# Core matching logic
# ---------------------------------------------------------------------------
def match_face(face_encoding: np.ndarray,
               known_encodings: List[np.ndarray],
               known_names: List[str],
               tolerance: float = MATCH_TOLERANCE) -> str:
    """
    Compare a single face encoding against all known encodings and return
    the name of the closest match within tolerance, or "Unknown" otherwise.
    """
    if not known_encodings:
        return "Unknown"

    distances = face_recognition.face_distance(known_encodings, face_encoding)
    best_match_index = int(np.argmin(distances))

    if distances[best_match_index] <= tolerance:
        return known_names[best_match_index]
    return "Unknown"


def detect_and_match_faces(
    frame_bgr: np.ndarray,
    known_encodings: List[np.ndarray],
    known_names: List[str],
) -> List[Tuple[Tuple[int, int, int, int], str]]:
    """
    Detect all faces in a frame and match each against known encodings.

    Returns a list of ((top, right, bottom, left), name) tuples with
    coordinates scaled to the ORIGINAL frame resolution.
    """
    rgb_frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    small_frame = cv2.resize(rgb_frame, (0, 0),
                              fx=FRAME_RESIZE_SCALE, fy=FRAME_RESIZE_SCALE)

    face_locations = face_recognition.face_locations(small_frame, model="hog")
    if not face_locations:
        return []

    face_encodings = face_recognition.face_encodings(small_frame, face_locations)

    scale = 1.0 / FRAME_RESIZE_SCALE
    results = []

    for (top, right, bottom, left), encoding in zip(face_locations, face_encodings):
        name = match_face(encoding, known_encodings, known_names)

        # Scale bounding box back up to original frame size
        box = (int(top * scale), int(right * scale),
               int(bottom * scale), int(left * scale))
        results.append((box, name))

    return results


# ---------------------------------------------------------------------------
# Drawing helper
# ---------------------------------------------------------------------------
def draw_face_boxes(frame: np.ndarray,
                    faces: List[Tuple[Tuple[int, int, int, int], str]],
                    num_registered: int) -> None:
    """Draw bounding boxes, name labels, and status bar on the frame in place."""
    h, w = frame.shape[:2]

    # Draw face boxes
    for (top, right, bottom, left), name in faces:
        color = BOX_COLOR_KNOWN if name != "Unknown" else BOX_COLOR_UNKNOWN

        # Draw box around face
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)

        # Label background
        label_text = name
        label_top = max(top - 25, 0)
        (tw, th), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        cv2.rectangle(frame, (left, label_top), (left + tw + 10, top), color, cv2.FILLED)
        cv2.putText(frame, label_text, (left + 5, top - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    # Status overlay bar at top
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 32), (20, 20, 20), cv2.FILLED)
    cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)

    if faces:
        status_text = f"Faces Detected: {len(faces)} | Press 'q' to quit"
    else:
        status_text = "Searching for faces... | Press 'q' to quit"

    if num_registered == 0:
        status_text += " (No registered faces - run register_face.py)"

    cv2.putText(frame, status_text, (10, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)


# ---------------------------------------------------------------------------
# Webcam detection loop
# ---------------------------------------------------------------------------
def run_detection_session(camera_index: int = 0) -> None:
    """Opens the webcam and runs continuous face detection + recognition."""
    known_encodings, known_names = load_known_faces()
    if known_names:
        unique_names = sorted(set(known_names))
        print(f"[INFO] Loaded {len(known_names)} registered face record(s): {', '.join(unique_names)}")
    else:
        print("[INFO] No known faces registered. All detected faces will be marked as 'Unknown'.")

    print("[INFO] Connecting to webcam...")
    cap, active_idx = open_camera(camera_index)
    if cap is None or not cap.isOpened():
        print("[ERROR] Could not access any webcam.")
        print("  Troubleshooting steps:")
        print("  1. Ensure no other application (Zoom, Teams, Chrome, Camera app) is using the camera.")
        print("  2. In Windows Settings > Privacy & security > Camera, enable 'Let desktop apps access your camera'.")
        print("  3. If using an external webcam, verify it is plugged in.")
        sys.exit(1)

    print(f"[INFO] Camera index {active_idx} started successfully.")
    print("[INFO] Real-time face detection is active. Press 'q' to quit.")

    frame_count = 0
    last_results: List[Tuple[Tuple[int, int, int, int], str]] = []

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                print("[ERROR] Failed to read frame from webcam.")
                break

            frame_count += 1

            # Only run the expensive detection+matching pipeline every N frames
            if frame_count % PROCESS_EVERY_N_FRAMES == 0:
                last_results = detect_and_match_faces(frame, known_encodings, known_names)

            draw_face_boxes(frame, last_results, len(known_names))
            cv2.imshow(WINDOW_NAME, frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                print("[INFO] Quitting detection session.")
                break

    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real-time face detection & recognition")
    parser.add_argument("--camera", type=int, default=0, help="Camera index (default: 0)")
    args = parser.parse_args()

    run_detection_session(camera_index=args.camera)
