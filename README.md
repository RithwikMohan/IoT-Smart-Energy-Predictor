# ⚡ IoT Smart Energy Predictor

**IoT Smart Energy Predictor** tracks and optimizes appliance power in real-time. Using an ESP32 and Kafka, it streams data to a web dashboard featuring dynamic budget rollovers, remote relay control, and Machine Learning to forecast costs and detect hardware anomalies instantly.

---

## ✨ Features

- **📡 Real-Time Telemetry:** Monitors live Power (Watts), Current (Amps), and daily electricity costs (₹) with zero latency.
- **🎛️ Remote Appliance Control:** Includes a two-way communication system to remotely toggle the physical power relay from the web dashboard.
- **🏦 Dynamic Budget Rollover:** Enforces a daily energy budget. Overspending shrinks tomorrow's budget, while energy savings roll over as a reward.
- **🔮 ML Forecasting:** Utilizes Huber Regression, Exponential Weighted Moving Averages (EWMA), and Linear trends to predict end-of-day and end-of-month energy costs.
- **🚨 Live Anomaly Detection:** Employs an Isolation Forest model and dynamic Z-Scores to instantly flag irregular power spikes or faulty appliances.
- **💎 Premium UI:** A fully responsive, glassmorphism-styled Streamlit dashboard with interactive charts and live exhaustion timers.

---

## 🛠️ Tech Stack & Architecture

### Hardware (Edge Computing)
- **ESP32 Microcontroller:** Reads analog sensor data and actuates relays.
- **ACS712 Current Sensor:** Measures the AC current draw of the appliance.
- **5V Relay Module:** Physically cuts or restores power to the appliance.

### Software Pipeline
- **Apache Kafka (WSL2):** High-throughput message broker handling live telemetry streams.
- **Python (Producer):** Reads serial data via `pyserial`, applies Exponential Moving Average (EMA) smoothing, calculates costs, and publishes JSON payloads to Kafka.
- **Streamlit (Consumer/Dashboard):** Consumes Kafka streams, visualizes data, runs Scikit-Learn ML models, and sends control commands back to the hardware.

---

## 📁 Project Structure

```text
├── current_calculation/
│   └── current_calculation.ino  # ESP32 C++ firmware for sensor reading & relay control
├── energy_monitor.py            # Python data ingestion, math smoothing, and Kafka Producer
├── dashboard.py                 # Streamlit frontend, Kafka Consumer, and ML models
├── realtime.py                  # CLI fallback for testing serial communication
└── README.md                    # Project documentation
```

---

## 🚀 Setup & Installation

### 1. Hardware Setup
1. Connect the **ACS712 OUT** pin to `GPIO 34` on the ESP32.
2. Connect the **Relay IN** pin to `GPIO 25` on the ESP32.
3. Flash the `current_calculation.ino` code to the ESP32 using the Arduino IDE.

### 2. Software Dependencies
Ensure you have Python 3.8+ installed. Install the required libraries:
```bash
pip install pyserial pandas streamlit kafka-python scikit-learn
```

### 3. Start Apache Kafka
Ensure Zookeeper and Kafka are running in your environment (e.g., WSL2 or Docker):
```bash
# Start Zookeeper
bin/zookeeper-server-start.sh config/zookeeper.properties

# Start Kafka
bin/kafka-server-start.sh config/server.properties
```

### 4. Run the Pipeline
First, start the Python data ingestion script. It will detect the ESP32 on the COM port and begin pushing data to Kafka.
```bash
python energy_monitor.py
```

Then, open a new terminal and launch the Streamlit dashboard:
```bash
python -m streamlit run dashboard.py
```

Open `http://localhost:8501` in your browser to view the live dashboard!

---

## ⚠️ Notes for Demonstration
The system currently includes a `TIME_SCALE` multiplier (720x speed) for demonstration purposes, compressing a 24-hour day into 2 minutes of real-world time to quickly visualize budget rollovers and month-long forecasting. To run in real-time, set `TIME_SCALE = 1.0` in `energy_monitor.py`.
