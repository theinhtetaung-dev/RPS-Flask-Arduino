import cv2
import mediapipe as mp
import random
import os
import serial
import serial.tools.list_ports
import threading
import time
import queue
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.vision import drawing_utils as mp_drawing
from mediapipe.tasks.python.vision import drawing_styles as mp_drawing_styles
from flask import Flask, Response, render_template, jsonify, send_from_directory

# Flask App
app = Flask(__name__)

# Arduino Serial Configuration
arduino_serial = None
DEFAULT_PORT = "COM3"
serial_queue = queue.Queue()

def get_arduino_port():
    ports = list(serial.tools.list_ports.comports())
    # 1st pass: Prioritize actual Arduino devices (like "Arduino Uno (COM11)")
    for p in ports:
        desc = p.description.lower()
        if "arduino" in desc:
            return p.device
    # 2nd pass: Fallback to other common serial descriptions
    for p in ports:
        desc = p.description.lower()
        hwid = p.hwid.lower()
        if "ch340" in desc or "usb" in desc or "serial" in desc:
            return p.device
    if ports:
        return ports[0].device
    return None

def init_arduino_serial():
    global arduino_serial
    port = get_arduino_port() or DEFAULT_PORT
    try:
        # Disable hardware flow control and add a write timeout to prevent thread hangs
        arduino_serial = serial.Serial(
            port, 
            9600, 
            timeout=1.0, 
            write_timeout=2.0,
            rtscts=False,
            dsrdtr=False
        )
        # Release DTR/RTS to prevent holding the Arduino in a reset state
        try:
            arduino_serial.dtr = False
            arduino_serial.rts = False
        except:
            pass
        arduino_serial.reset_input_buffer()
        arduino_serial.reset_output_buffer()
        print(f"Startup: Connected to Arduino on port: {port}")
    except Exception as e:
        print(f"Startup: Failed to connect to Arduino on port {port}: {e}")

# Start serial connection in background so it doesn't block Flask and finishes resetting early
threading.Thread(target=init_arduino_serial, daemon=True).start()

def serial_worker():
    global arduino_serial
    while True:
        try:
            # Block until a choice is available
            user_choice = serial_queue.get()
            
            # Ensure connection is open
            if arduino_serial is None or not arduino_serial.is_open:
                port = get_arduino_port() or DEFAULT_PORT
                try:
                    arduino_serial = serial.Serial(
                        port, 
                        9600, 
                        timeout=1.0, 
                        write_timeout=2.0,
                        rtscts=False,
                        dsrdtr=False
                    )
                    try:
                        arduino_serial.dtr = False
                        arduino_serial.rts = False
                    except:
                        pass
                    arduino_serial.reset_input_buffer()
                    arduino_serial.reset_output_buffer()
                    print(f"Worker: Connected to Arduino on port: {port}")
                    time.sleep(2.0)  # Wait for Arduino to finish resetting
                except Exception as e:
                    print(f"Worker: Connection failed: {e}")
                    arduino_serial = None
                    serial_queue.task_done()
                    continue

            if arduino_serial and arduino_serial.is_open:
                try:
                    char_map = {"Rock": "R", "Paper": "P", "Scissors": "S"}
                    if user_choice in char_map:
                        message = char_map[user_choice]
                        arduino_serial.write(message.encode('utf-8'))
                        print(f"Worker: Sent to Arduino: {message}")
                except Exception as e:
                    print(f"Worker: Error sending data: {e}")
                    try:
                        arduino_serial.close()
                    except:
                        pass
                    arduino_serial = None
            
            serial_queue.task_done()
        except Exception as e:
            print(f"Worker exception: {e}")

# Start the background worker thread for serial operations
threading.Thread(target=serial_worker, daemon=True).start()

def send_to_arduino(user_choice):
    # Enqueue choice to be sent asynchronously to avoid blocking Flask/OpenCV
    serial_queue.put(user_choice)

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

class VideoCaptureThread:
    def __init__(self, source):
        self.cap = cv2.VideoCapture(source)
        # Set buffer size to 1 to reduce lag if supported by the backend
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.ret = False
        self.frame = None
        self.frame_id = 0
        self.lock = threading.Lock()
        self.running = True
        self.frame_ready = threading.Event()
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _update(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                with self.lock:
                    self.frame = frame
                    self.ret = ret
                    self.frame_id += 1
                self.frame_ready.set()
            else:
                time.sleep(0.01)

    def read(self):
        if not self.frame_ready.is_set():
            self.frame_ready.wait(timeout=5.0)
        with self.lock:
            if self.frame is not None:
                return self.ret, self.frame.copy(), self.frame_id
            return False, None, 0

    def release(self):
        self.running = False
        self.thread.join(timeout=1.0)
        self.cap.release()

# OpenCV Camera (Supports CAMERA_SOURCE environment variable for DroidCam indices or IP URLs)
camera_source = os.environ.get("CAMERA_SOURCE", "0")
if camera_source.isdigit():
    camera_source = int(camera_source)

camera = VideoCaptureThread(camera_source)

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

# Game state for 5-match sequence
game_allowed_wins = random.choice([0, 1])
game_user_wins = 0
game_match_count = 0
game_draw_count = 0
game_lose_count = 0


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
    Count extended fingers by rotating the hand landmarks to be vertical
    relative to the wrist, then applying vertical counting logic.
    This makes the detection rotation-invariant.
    """
    import math

    class RotatedLandmark:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    wrist = landmarks[0]
    mid_mcp = landmarks[9]

    # Calculate direction vector from wrist to middle finger MCP
    dx = mid_mcp.x - wrist.x
    dy = mid_mcp.y - wrist.y

    # Angle of the hand direction vector
    theta = math.atan2(dy, dx)
    
    # Angle to rotate by to make the hand point straight up (-pi/2)
    alpha = -math.pi / 2.0 - theta

    cos_a = math.cos(alpha)
    sin_a = math.sin(alpha)

    # Rotate all landmarks around the wrist
    rotated = []
    for lm in landmarks:
        tx = lm.x - wrist.x
        ty = lm.y - wrist.y
        rx = tx * cos_a - ty * sin_a + wrist.x
        ry = tx * sin_a + ty * cos_a + wrist.y
        rotated.append(RotatedLandmark(rx, ry))

    open_count = 0

    # Thumb: dynamic x-axis comparison based on index vs pinky position
    index_mcp_x = rotated[5].x
    pinky_mcp_x = rotated[17].x

    if index_mcp_x > pinky_mcp_x:
        if rotated[THUMB_TIP].x > rotated[THUMB_IP].x:
            open_count += 1
    else:
        if rotated[THUMB_TIP].x < rotated[THUMB_IP].x:
            open_count += 1

    # Index–Pinky: tip above pip in rotated coordinates (y decreases upward)
    for tip_id, pip_id in zip(FINGER_TIPS, FINGER_PIPS):
        if rotated[tip_id].y < rotated[pip_id].y:
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

    last_frame_id = -1
    while True:
        success, frame, frame_id = camera.read()
        if not success:
            break

        if frame_id == last_frame_id:
            time.sleep(0.005)
            continue
        last_frame_id = frame_id

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


@app.route("/recording/<path:filename>")
def serve_recording(filename):
    """Serve game audio recording files."""
    return send_from_directory("Recording", filename)


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


@app.route("/reset_game", methods=["POST"])
def reset_game():
    """Reset the game state for a new 5-match sequence."""
    global game_allowed_wins, game_user_wins, game_match_count, game_draw_count, game_lose_count
    game_allowed_wins = random.choice([0, 1])
    game_user_wins = 0
    game_match_count = 0
    game_draw_count = 0
    game_lose_count = 0
    return jsonify({"status": "success", "allowed_wins": game_allowed_wins})


@app.route("/resolve")
def resolve():
    """
    Snapshot the current player gesture, pick AI move, compute result.
    Returns JSON with player, ai_name, ai_emoji, result, and progress stats.
    """
    global game_allowed_wins, game_user_wins, game_match_count, game_draw_count, game_lose_count

    player = current_gesture
    if player in ("No Hand", "Unknown"):
        return jsonify({"error": "No valid gesture detected"}), 400

    cheat_sheet = {
        "Rock": "Paper",
        "Paper": "Scissors",
        "Scissors": "Rock"
    }

    game_match_count += 1

    if game_allowed_wins > 0:
        choices = ["Rock", "Paper", "Scissors"]
        ai_name = random.choice(choices)
        result = determine_result(player, ai_name)
        
        if result == "WIN":
            game_allowed_wins -= 1
            game_user_wins += 1
        elif result == "DRAW":
            game_draw_count += 1
        else:
            game_lose_count += 1
    else:
        ai_name = cheat_sheet.get(player, "Rock")
        result = determine_result(player, ai_name)
        game_lose_count += 1

    # Find the choice object in AI_CHOICES
    ai = next((c for c in AI_CHOICES if c["name"] == ai_name), AI_CHOICES[0])

    # Send ONLY the AI choice to Arduino
    send_to_arduino(ai_name)

    game_over = (game_match_count >= 5)

    # Determine which audio recording to play based on outcome
    audio_path = None
    if result == "WIN":
        audio_path = "UserWin/HanZaw-UserWin.ogg"
    elif result == "DRAW":
        audio_path = "Draw/HanZaw.ogg"
    elif result == "LOSE":
        audio_path = random.choice(["UserLose/HanZaw-UserLose.ogg", "UserLose/HanZaw-UserLose2.ogg"])

    return jsonify({
        "player":       player,
        "ai_name":      ai["name"],
        "ai_emoji":     ai["emoji"],
        "result":       result,
        "match_count":  game_match_count,
        "user_wins":    game_user_wins,
        "draw_count":   game_draw_count,
        "lose_count":   game_lose_count,
        "game_over":    game_over,
        "audio_url":    f"/recording/{audio_path}" if audio_path else None,
    })


# ── Entry Point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
