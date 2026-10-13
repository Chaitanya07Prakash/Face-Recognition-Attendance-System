"""
register_face.py
-----------------
Captures a user's face via webcam, extracts a 128-d face encoding using the
`face_recognition` library, and persists it (along with the user's name/ID)
to `data/trained_faces.pkl`.

Usage:
    python register_face.py
    python register_face.py --name "John Doe"
    python register_face.py --name "John Doe" --camera 0

Controls:
    's' -> capture current frame and register face
    'q' -> quit without registering

Design notes:
    - Frames are resized before face detection to keep the webcam preview
      responsive; encoding is computed on the original RGB frame for quality.
    - Encodings are stored as a list of dicts: {"name": str, "encoding": np.ndarray}
      in `data/trained_faces.pkl` so `detect_face.py` can load and match against them.
    - Uses DirectShow on Windows for instant, reliable camera initialization.
"""

import argparse
import os
import pickle
import sys
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import cv2
import face_recognition
import numpy as np

# ---------------------------------------------------------------------------
# Configuration & Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

def get_data_file() -> Path:
    """Find or create trained_faces.pkl in the shared data directory."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / "trained_faces.pkl"

DATA_FILE = get_data_file()
FRAME_RESIZE_SCALE = 0.25                # downscale factor for detection speed
WINDOW_NAME = "Register Face - press 's' to capture, 'q' to quit"


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
def load_known_faces(data_file: Optional[Path] = None) -> List[Dict]:
    """Load existing (name, encoding) records from disk, if any."""
    target = data_file or get_data_file()
    if target.exists():
        try:
            with open(target, "rb") as f:
                return pickle.load(f)
        except Exception as exc:
            print(f"[WARN] Failed to read {target}: {exc}")
    return []


def save_known_faces(records: List[Dict], data_file: Optional[Path] = None) -> None:
    """Persist (name, encoding) records to disk."""
    target = data_file or get_data_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as f:
        pickle.dump(records, f)


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
# Core face-encoding logic
# ---------------------------------------------------------------------------
def extract_face_encoding(frame_bgr: np.ndarray) -> Tuple[Optional[np.ndarray], Optional[str]]:
    """
    Given a BGR frame, detect faces and return (encoding, None) on success,
    or (None, error_message) on failure.
    """
    rgb_frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    small_frame = cv2.resize(rgb_frame, (0, 0),
                              fx=FRAME_RESIZE_SCALE, fy=FRAME_RESIZE_SCALE)

    face_locations = face_recognition.face_locations(small_frame, model="hog")

    if len(face_locations) == 0:
        return None, "No face detected. Face the camera directly."

    if len(face_locations) > 1:
        return None, "Multiple faces detected. Ensure only 1 person is in frame."

    # Scale the single detected face location back to full resolution
    top, right, bottom, left = face_locations[0]
    scale = 1.0 / FRAME_RESIZE_SCALE
    face_location_full_res = (
        int(top * scale), int(right * scale),
        int(bottom * scale), int(left * scale)
    )

    encodings = face_recognition.face_encodings(
        rgb_frame, known_face_locations=[face_location_full_res]
    )

    if not encodings:
        return None, "Could not compute encoding. Please try again."

    return encodings[0], None


def register_new_face(name: str, encoding: np.ndarray) -> None:
    """Append a new (name, encoding) record to the persisted data file."""
    records = load_known_faces()
    records.append({"name": name, "encoding": encoding})
    save_known_faces(records)
    print(f"[INFO] Successfully registered '{name}'. Total registered faces: {len(records)}")


# ---------------------------------------------------------------------------
# Webcam capture loop
# ---------------------------------------------------------------------------
def run_registration_session(name: Optional[str] = None, camera_index: int = 0) -> None:
    """
    Opens the webcam, shows a live preview, and lets the user capture a
    frame (press 's') to register their face.
    """
    if not name:
        try:
            name = input("Enter your name or ID for registration: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[INFO] Registration cancelled.")
            sys.exit(0)

    if not name:
        print("[ERROR] Name cannot be empty. Exiting.")
        sys.exit(1)

    print("[INFO] Connecting to webcam...")
    cap, active_idx = open_camera(camera_index)
    if cap is None or not cap.isOpened():
        print("[ERROR] Could not access any webcam.")
        print("  Troubleshooting steps:")
        print("  1. Ensure no other application (Zoom, Teams, Chrome, Camera app) is using the camera.")
        print("  2. In Windows Settings > Privacy & security > Camera, enable 'Let desktop apps access your camera'.")
        print("  3. If using an external webcam, verify it is plugged in.")
        sys.exit(1)

    print(f"[INFO] Camera index {active_idx} started.")
    print(f"[INFO] Ready to register face for: '{name}'")
    print("       Press 's' to capture and save, 'q' to quit.")

    status_msg = f"Registering: {name} | Press 's' to Capture, 'q' to Cancel"
    status_color = (0, 255, 255)

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                print("[ERROR] Failed to read frame from webcam.")
                break

            # Mirror the preview for intuitive user alignment
            display_frame = cv2.flip(frame, 1)
            h, w = display_frame.shape[:2]

            # Top overlay bar
            overlay = display_frame.copy()
            cv2.rectangle(overlay, (0, 0), (w, 36), (20, 20, 20), cv2.FILLED)
            cv2.addWeighted(overlay, 0.7, display_frame, 0.3, 0, display_frame)

            cv2.putText(display_frame, status_msg, (12, 24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, status_color, 2)

            cv2.imshow(WINDOW_NAME, display_frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord('s'):
                # Extract encoding from non-flipped frame
                encoding, err = extract_face_encoding(frame)
                if encoding is not None:
                    register_new_face(name, encoding)
                    status_msg = f"SUCCESS! Registered '{name}'."
                    status_color = (0, 255, 0)
                    # Show success frame briefly
                    overlay = display_frame.copy()
                    cv2.rectangle(overlay, (0, 0), (w, 36), (0, 150, 0), cv2.FILLED)
                    cv2.addWeighted(overlay, 0.8, display_frame, 0.2, 0, display_frame)
                    cv2.putText(display_frame, status_msg, (12, 24),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                    cv2.imshow(WINDOW_NAME, display_frame)
                    cv2.waitKey(1200)
                    break
                else:
                    status_msg = f"WARNING: {err}"
                    status_color = (0, 0, 255)
                    print(f"[WARN] {err}")

            elif key == ord('q'):
                print("[INFO] Registration cancelled by user.")
                break

    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Register a new face in the attendance system")
    parser.add_argument("--name", type=str, default="", help="Name/ID of the person to register")
    parser.add_argument("--camera", type=int, default=0, help="Camera index (default: 0)")
    args = parser.parse_args()

    run_registration_session(name=args.name, camera_index=args.camera)
