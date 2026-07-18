const int rockPin = 13;
const int paperPin = 12;
const int scissorsPin = 11;

void setup() {
  // Increased baud rate to 115200 for ultra-fast serial communication
  Serial.begin(115200);

  pinMode(rockPin, OUTPUT);
  pinMode(paperPin, OUTPUT);
  pinMode(scissorsPin, OUTPUT);

  // Ensure all LEDs are in the closed (OFF) condition initially
  digitalWrite(rockPin, LOW);
  digitalWrite(paperPin, LOW);
  digitalWrite(scissorsPin, LOW);
}

void loop() {
  // Check if exactly one byte (or more) is available
  if (Serial.available() > 0) {
    // Read the single character command instantly (No waiting for '\n')
    char command = Serial.read();

    // Reset all LEDs to OFF before starting the new animation
    digitalWrite(rockPin, LOW);
    digitalWrite(paperPin, LOW);
    digitalWrite(scissorsPin, LOW);

    switch (command) {
      case 'R': // Rock -> Pin 13 ON for 1 second
        digitalWrite(rockPin, HIGH);
        delay(3000);
        digitalWrite(rockPin, LOW);
        break;

      case 'P': // Paper -> Pin 12 ON for 1 second
        digitalWrite(paperPin, HIGH);
        delay(3000);
        digitalWrite(paperPin, LOW);
        break;

      case 'S': // Scissors -> Pin 11 two flip-flops in 1 second
        digitalWrite(scissorsPin, HIGH);
        delay(3000);
        digitalWrite(scissorsPin, LOW);
        // delay(1000);
        // digitalWrite(scissorsPin, HIGH);
        // delay(250);
        // digitalWrite(scissorsPin, LOW);
        delay(250); // Total = 1000ms (1 second)
        break;
        
      default:
        // Ignore any other unexpected characters (like newlines)
        break;
    }
  }
}