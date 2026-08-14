import cv2
import mediapipe as mp
import random
import os
import serial
import serial.tools.list_ports
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.vision import drawing_utils as mp_drawing
from mediapipe.tasks.python.vision import drawing_styles as mp_drawing_styles
from flask import Flask, Response, render_template, jsonify

# Flask App
app = Flask(__name__)

# Arduino Serial Configuration
arduino_serial = None
DEFAULT_PORT = "COM3"

def get_arduino_port():
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        desc = p.description.lower()
        hwid = p.hwid.lower()
        if "arduino" in desc or "ch340" in desc or "usb" in desc or "serial" in desc:
            return p.device
    if ports:
        return ports[0].device
    return None

def send_to_arduino(user_choice):
    global arduino_serial
    # Try to initialize if not currently connected
    if arduino_serial is None:
        port = get_arduino_port() or DEFAULT_PORT
        try:
            # IMPORTANT: Baud rate increased to 115200 to match Arduino
            arduino_serial = serial.Serial(port, 115200, timeout=0.1, write_timeout=0.1)
            print(f"Connected to Arduino on port: {port}")
        except Exception as e:
            pass

    if arduino_serial and arduino_serial.is_open:
        try:
            # Map choice to a single byte for minimal payload
            char_map = {"Rock": "R", "Paper": "P", "Scissors": "S"}
            
            if user_choice in char_map:
                # Send just the single character (e.g., "R") without newlines
                message = char_map[user_choice]
                arduino_serial.write(message.encode('utf-8'))
                print(f"Sent to Arduino: {message}")
                
        except Exception as e:
            print(f"Error sending/receiving data: {e}")
            try:
                arduino_serial.close()
            except:
                pass
            arduino_serial = None

# MediaPipe Hand Landmarker (Tasks API >= 0.10)
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hand_landmarker.task")

hand_options = mp_vision.HandLandmarkerOptions(
    base_options=mp_python.BaseOptions(model_asset_path=MODEL_PATH),
    num_hands=1,
    min_hand_detection_confidence=0.7,
    min_hand_presence_confidence=0.7,
    min_tracking_confidence=0.5,
    running_mode=mp_vision.RunningMode.VIDEO,
)
landmarker = mp_vision.HandLandmarker.create_from_options(hand_options)

# OpenCV Camera
camera = cv2.VideoCapture(0)
camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

# Finger Landmark IDs
FINGER_TIPS = [8, 12, 16, 20]
FINGER_PIPS = [6, 10, 14, 18]
THUMB_TIP   = 4
THUMB_IP    = 3

# Global state — updated every frame, read by /current_gesture
current_gesture = "No Hand"
_timestamp_ms   = 0

# RPS choices for AI
AI_CHOICES = [
    {"name": "Rock",     "emoji": "✊"},
    {"name": "Scissors", "emoji": "✌️"},
    {"name": "Paper",    "emoji": "✋"},
]


# ── Gesture helpers ────────────────────────────────────────────────────────────

def get_hand_orientation(landmarks) -> str:
    """
    Determine if the hand is roughly vertical or horizontal.
    Compares the wrist (0) to middle-finger MCP (9) vector.
    Returns 'vertical' when fingers point up/down,
            'horizontal' when fingers point left/right.
    """
    wrist = landmarks[0]
    mid_mcp = landmarks[9]   # base of middle finger

    dx = abs(mid_mcp.x - wrist.x)
    dy = abs(mid_mcp.y - wrist.y)

    # If the hand's length runs more along x than y → horizontal
    return "horizontal" if dx > dy else "vertical"


def count_open_fingers(landmarks, handedness_label: str) -> int:
    """
    Count extended fingers.
    - Vertical hand  (✋): fingers extend upward  → use y-axis comparison.
    - Horizontal hand (🫱): fingers extend sideways → use x-axis comparison.
    The thumb always uses the opposite axis to the fingers.
    """
    open_count = 0
    orientation = get_hand_orientation(landmarks)

    if orientation == "vertical":
        # ── Vertical mode (original logic) ────────────────────────────────
        # Thumb: x-axis (moves left/right when hand is upright)
        # After mirroring, physical Right hand → 'Left' label in MediaPipe
        if handedness_label == "Left":
            if landmarks[THUMB_TIP].x < landmarks[THUMB_IP].x:
                open_count += 1
        else:
            if landmarks[THUMB_TIP].x > landmarks[THUMB_IP].x:
                open_count += 1

        # Index–Pinky: tip above pip in image coords (y decreases upward)
        for tip_id, pip_id in zip(FINGER_TIPS, FINGER_PIPS):
            if landmarks[tip_id].y < landmarks[pip_id].y:
                open_count += 1

    else:
        # ── Horizontal mode (🫱 / 🫲) ──────────────────────────────────────
        # Fingers point sideways, so extension is along the x-axis.
        # Determine which direction the fingers point:
        #   wrist x < mid_mcp x  → fingers point RIGHT
        #   wrist x > mid_mcp x  → fingers point LEFT
        fingers_point_right = landmarks[9].x > landmarks[0].x

        # Thumb: now extends along y-axis when hand is horizontal
        if fingers_point_right:
            # Right-pointing hand: thumb tip above thumb IP
            if landmarks[THUMB_TIP].y < landmarks[THUMB_IP].y:
                open_count += 1
        else:
            # Left-pointing hand: thumb tip below thumb IP
            if landmarks[THUMB_TIP].y > landmarks[THUMB_IP].y:
                open_count += 1

        # Index–Pinky: tip further along x than pip
        for tip_id, pip_id in zip(FINGER_TIPS, FINGER_PIPS):
            if fingers_point_right:
                if landmarks[tip_id].x > landmarks[pip_id].x:
                    open_count += 1
            else:
                if landmarks[tip_id].x < landmarks[pip_id].x:
                    open_count += 1

    return open_count


def classify_gesture(n: int) -> str:
    """Map open-finger count to Rock / Scissors / Paper."""
    if n == 0:
        return "Rock"
    elif n == 2:
        return "Scissors"
    elif n == 5:
        return "Paper"
    else:
        return "Unknown"


def determine_result(player: str, ai: str) -> str:
    """Return WIN / LOSE / DRAW based on RPS rules."""
    if player == ai:
        return "DRAW"
    wins = {("Rock", "Scissors"), ("Scissors", "Paper"), ("Paper", "Rock")}
    return "WIN" if (player, ai) in wins else "LOSE"


# ── Video Frame Generator ──────────────────────────────────────────────────────

def generate_frames():
    global current_gesture, _timestamp_ms

    while True:
        success, frame = camera.read()
        if not success:
            break

        # Mirror for natural selfie view
        frame = cv2.flip(frame, 1)

        # BGR → RGB for MediaPipe
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image  = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

        # VIDEO mode needs a monotonically increasing timestamp
        _timestamp_ms += 33
        result = landmarker.detect_for_video(mp_image, _timestamp_ms)

        gesture_text = "No Hand"

        if result.hand_landmarks:
            for idx, hand_landmarks in enumerate(result.hand_landmarks):
                label = result.handedness[idx][0].display_name

                # Draw skeleton
                mp_drawing.draw_landmarks(
                    frame,
                    hand_landmarks,
                    mp_vision.HandLandmarksConnections.HAND_CONNECTIONS,
                    mp_drawing_styles.get_default_hand_landmarks_style(),
                    mp_drawing_styles.get_default_hand_connections_style(),
                )

                n = count_open_fingers(hand_landmarks, label)
                gesture_text = classify_gesture(n)

        # Update global so /current_gesture can read it
        current_gesture = gesture_text

        # Overlay banner
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (640, 50), (15, 15, 35), -1)
        cv2.addWeighted(overlay, 0.65, frame, 0.35, 0, frame)

        cv2.putText(
            frame,
            f"Your Hand: {gesture_text}",
            (12, 35),
            cv2.FONT_HERSHEY_DUPLEX,
            0.72,
            (255, 220, 80),
            2,
            cv2.LINE_AA,
        )

        ret, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ret:
            continue
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
        )


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/video_feed")
def video_feed():
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/current_gesture")
def get_current_gesture():
    """Return the player's currently detected gesture as JSON."""
    return jsonify({"gesture": current_gesture})


@app.route("/ai_choice")
def get_ai_choice():
    """Return a random AI pick as JSON."""
    choice = random.choice(AI_CHOICES)
    return jsonify(choice)


@app.route("/resolve")
def resolve():
    """
    Snapshot the current player gesture, pick AI move, compute result.
    Returns JSON with player, ai_name, ai_emoji, result.
    """
    player = current_gesture
    if player in ("No Hand", "Unknown"):
        return jsonify({"error": "No valid gesture detected"}), 400

    # 1. Map player gesture to the winning choice for the computer
    if player == "Rock":
        ai_name = "Paper"
    elif player == "Paper":
        ai_name = "Scissors"
    elif player == "Scissors":
        ai_name = "Rock"
    else:
        ai_name = "Rock"

    # Find the choice object in AI_CHOICES
    ai = next((c for c in AI_CHOICES if c["name"] == ai_name), AI_CHOICES[0])
    result = determine_result(player, ai["name"])

    # Send ONLY the user choice to Arduino
    send_to_arduino(player)

    return jsonify({
        "player":    player,
        "ai_name":   ai["name"],
        "ai_emoji":  ai["emoji"],
        "result":    result,
    })


# ── Entry Point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
