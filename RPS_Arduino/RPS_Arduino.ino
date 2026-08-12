#include <Servo.h>

// Define pins for servos
const int rockPin = 9;      // Servo 1
const int paperPin = 10;    // Servo 2
const int scissorsPin = 11; // Servo 3

// Create Servo objects
Servo rockServo;
Servo paperServo;
Servo scissorsServo;

// Define servo movement angles (in degrees)
const int REST_POS = 0;     // Initial/Resting angle
const int ACTION_POS = 90;  // Triggered angle (change to 180 if needed)

void setup() {
  // Ultra-fast serial communication
  Serial.begin(115200);

  // Attach servos to their respective pins
  rockServo.attach(rockPin);
  paperServo.attach(paperPin);
  scissorsServo.attach(scissorsPin);

  // Set initial position to 0 degrees
  rockServo.write(REST_POS);
  paperServo.write(REST_POS);
  scissorsServo.write(REST_POS);
}

void loop() {
  // Check if character is available in serial buffer
  if (Serial.available() > 0) {
    char command = Serial.read();

    // Ensure all servos are back at resting position before executing
    rockServo.write(REST_POS);
    paperServo.write(REST_POS);
    scissorsServo.write(REST_POS);

    switch (command) {
      case 'R': // Rock -> Servo 1 (Pin 9)
        rockServo.write(ACTION_POS);
        delay(3000); // Hold action for 3 seconds
        rockServo.write(REST_POS);
        break;

      case 'P': // Paper -> Servo 2 (Pin 10)
        paperServo.write(ACTION_POS);
        delay(3000); // Hold action for 3 seconds
        paperServo.write(REST_POS);
        break;

      case 'S': // Scissors -> Servo 3 (Pin 11)
        scissorsServo.write(ACTION_POS);
        delay(3000); // Hold action for 3 seconds
        scissorsServo.write(REST_POS);
        break;

      default:
        // Ignore unexpected characters (such as '\n' or '\r')
        break;
    }
  }
}