/*
 * ESP32 + ACS712 High-Precision Power Monitor
 * Version 11/10: Auto-calibrating, Self-healing, ML-Ready
 */

// --- Hardware Pins ---
const int sensorPin = 34;
const int relayPin  = 26;
const bool RELAY_ACTIVE_HIGH = true; // Set false if your relay module is active-low.

// --- System Constants ---
const float sensitivity   = 0.185;       // 5A ACS712 Model
const float voltageSupply = 230.0;       // Mains Voltage for fan/load estimation
const float alpha         = 0.2;         // EMA Smoothing Factor
const float scalingFactor = 0.57;        // Correction for low-current non-linearity
const float adcFactor     = 3.3 / 4095.0; // ESP32 12-bit ADC conversion multiplier

// --- Global State Variables ---
float zeroOffset = 0; 
float filteredCurrent = 0;
bool relayEnabled = true;

void setRelayState(bool on) {
  int pinLevel;
  if (RELAY_ACTIVE_HIGH) {
    pinLevel = on ? HIGH : LOW;
  } else {
    pinLevel = on ? LOW : HIGH;
  }
  digitalWrite(relayPin, pinLevel);
}

void handleSerialCommands() {
  if (!Serial.available()) {
    return;
  }

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  cmd.toUpperCase();

  if (cmd == "OFF") {
    relayEnabled = false;
    setRelayState(false);
    Serial.println("CMD_RX:OFF");
    Serial.println("CMD_ACK:OFF");
    Serial.println("RELAY_STATE:OFF");
  } else if (cmd == "ON") {
    relayEnabled = true;
    setRelayState(true);
    Serial.println("CMD_RX:ON");
    Serial.println("CMD_ACK:ON");
    Serial.println("RELAY_STATE:ON");
  } else if (cmd.length() > 0) {
    Serial.print("CMD_UNKNOWN:");
    Serial.println(cmd);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(relayPin, OUTPUT);
  
  // 1. ESP32 ADC Hardware Optimization
  analogReadResolution(12);           
  analogSetAttenuation(ADC_11db);     

  // 2. Cold-Start Thermal Stabilization
  setRelayState(false);
  Serial.println("SYSTEM_STARTUP");
  delay(5000); // Allow sensor and power rails to reach thermal equilibrium

  // 3. Precision Baseline Calibration
  float sensorSum = 0;
  for (int i = 0; i < 500; i++) {
    sensorSum += analogRead(sensorPin) * adcFactor;
    delayMicroseconds(200); 
  }
  zeroOffset = sensorSum / 500;
  
  Serial.print("CALIBRATED_OFFSET:");
  Serial.println(zeroOffset, 4);
  Serial.println("SYSTEM_READY");
  // 4. Start with the load OFF and wait for dashboard commands.
  relayEnabled = false;
  setRelayState(false);
  Serial.println("RELAY_STATE:OFF");
}

void loop() {
  // Handle commands from Python before sensor processing.
  handleSerialCommands();

  // 5. Oversampling for Noise Reduction
  float voltageSum = 0;
  int numSamples = 100;

  for (int i = 0; i < numSamples; i++) {
    voltageSum += analogRead(sensorPin) * adcFactor;
    delayMicroseconds(200); 
  }
  float avgVoltage = voltageSum / numSamples;

  // 6. Hardware Fail-Safe (Sensor Disconnect Detection)
  // If voltage drops to 0 or spikes to max, the wire is likely unplugged
  if (avgVoltage < 0.1 || avgVoltage > 3.2) {
    Serial.println("ERROR:SENSOR_DISCONNECTED");
    delay(1000);
    return; // Skip the rest of the loop until fixed
  }

  // 7. Current Calculation
  float rawCurrent = (avgVoltage - zeroOffset) / sensitivity;
  float absCurrent = abs(rawCurrent);

  // 8. Noise Floor & Software Fuse
  if (absCurrent < 0.03) absCurrent = 0; 
  
  // Clamp based on expected max load (~0.1A); prevents wild spikes from ADC noise
  if (absCurrent > 0.30) absCurrent = 0.30; 

  // 9. Dynamic Thermal Drift Calibration (Self-Healing)
  // If the fan is functionally off/drawing no measurable current, continuously 
  // micro-adjust the zero offset to account for temperature changes in the room.
  if (absCurrent == 0) {
    zeroOffset = (0.999 * zeroOffset) + (0.001 * avgVoltage);
  }

  // 10. Exponential Moving Average (EMA) Filter
  filteredCurrent = (alpha * absCurrent) + ((1.0 - alpha) * filteredCurrent);

  // 11. Final Calibrated Math
  float calibratedCurrent = filteredCurrent * scalingFactor;
  float power = voltageSupply * calibratedCurrent;

  // 12. ML/Python Optimized Output Array
  // Format: "I:0.123,P:0.61"
  Serial.print("I:");
  Serial.print(calibratedCurrent, 3);
  Serial.print(",P:");
  Serial.println(power, 2);

  delay(1000); // 1Hz logging frequency for clean, continuous dataset
}