#include <Servo.h>

// Create servo objects
Servo servo1; // Connected to Pin 3 Little finger (Pinky) Servo 
Servo servo2; // Connected to Pin 9 Thumb Servo  
Servo servo3; // Connected to Pin 6 Ring finger servo
Servo servo4; // Connected to Pin 11 Middle finger Servo
Servo servo5; // Connected to Pin 10 Index finger Servo

// Servo target angles (closed positions)
const int PINKY_CLOSE   = 110; // Pin 3
const int THUMB_CLOSE   = 80;  // Pin 9
const int RING_CLOSE    = 110; // Pin 6
const int MIDDLE_CLOSE  = 160; // Pin 11
const int INDEX_CLOSE   = 110; // Pin 10

// Servo rest angles (open positions)
const int PINKY_OPEN    = 0;
const int THUMB_OPEN    = 0;
const int RING_OPEN     = 0;
const int MIDDLE_OPEN   = 0;
const int INDEX_OPEN    = 0;

// Non-blocking state machine definitions
enum HandState {
  STATE_IDLE,
  STATE_ACTIVE_POSE,
  STATE_RETURNING
};

HandState currentState = STATE_IDLE;
unsigned long stateStartTime = 0;
const unsigned long POSE_DURATION = 1500;
const unsigned long RETURN_DURATION = 1500;

void setup() {
  // Serial communication at 115200 baud for stability
  Serial.begin(115200);

  // Attach servos to their respective Arduino pins
  servo1.attach(3);
  servo2.attach(9);
  servo3.attach(6);
  servo4.attach(11);
  servo5.attach(10);

  // Initialize all servos to closed position (initial state must be all close)
  servo1.write(PINKY_CLOSE);
  servo2.write(THUMB_CLOSE);
  servo3.write(RING_CLOSE);
  servo4.write(MIDDLE_CLOSE);
  servo5.write(INDEX_CLOSE);
  delay(1000); // Give servos time to reach starting position
}

void loop() {
  // 1. Process incoming serial data (only when hand is in IDLE state)
  if (currentState == STATE_IDLE && Serial.available() > 0) {
    char command = Serial.read();
    bool validCommand = false;

    switch (command) {
      case 'R': // Rock -> Close all fingers (target angles)
        servo1.write(PINKY_CLOSE);
        servo2.write(THUMB_CLOSE);
        servo3.write(RING_CLOSE);
        servo4.write(MIDDLE_CLOSE);
        servo5.write(INDEX_CLOSE);
        validCommand = true;
        break;

      case 'P': // Paper -> Open all fingers (all 0)
        servo1.write(PINKY_OPEN);
        servo2.write(THUMB_OPEN);
        servo3.write(RING_OPEN);
        servo4.write(MIDDLE_OPEN);
        servo5.write(INDEX_OPEN);
        validCommand = true;
        break;

      case 'S': // Scissors -> Index and Middle open, others closed
        servo1.write(PINKY_CLOSE);
        servo2.write(THUMB_CLOSE);
        servo3.write(RING_CLOSE);
        servo4.write(MIDDLE_OPEN);
        servo5.write(INDEX_OPEN);
        validCommand = true;
        break;

      default:
        // Ignore unexpected characters (such as '\n' or '\r')
        break;
    }

    if (validCommand) {
      currentState = STATE_ACTIVE_POSE;
      stateStartTime = millis();
    }
  }

  // 2. Non-blocking state machine updates using millis()
  if (currentState == STATE_ACTIVE_POSE) {
    if (millis() - stateStartTime >= POSE_DURATION) {
      // Reset all 5 servos back to closed position (resting state)
      servo1.write(PINKY_CLOSE);
      servo2.write(THUMB_CLOSE);
      servo3.write(RING_CLOSE);
      servo4.write(MIDDLE_CLOSE);
      servo5.write(INDEX_CLOSE);

      currentState = STATE_RETURNING;
      stateStartTime = millis();
    }
  } 
  else if (currentState == STATE_RETURNING) {
    if (millis() - stateStartTime >= RETURN_DURATION) {
      // Transition back to IDLE so the hand is ready to receive new commands
      currentState = STATE_IDLE;
    }
  }
}