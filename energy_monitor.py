import serial
import time
import re
import csv
import json
import os
from serial.tools import list_ports

KAFKA_TOPIC = "energy-data"
KAFKA_CONTROL_TOPIC = "energy-control"
DEFAULT_MONTHLY_BUDGET = 300.0
DAY_LENGTH_SECONDS = 120.0
TIME_SCALE = 720.0  # 86400 simulated seconds / 120 real seconds

# ── Kafka Producer Setup ──────────────────────────────────
try:
    from kafka import KafkaConsumer, KafkaProducer
    producer = KafkaProducer(
        bootstrap_servers='localhost:9092',
        value_serializer=lambda v: json.dumps(v).encode('utf-8')
    )
    control_consumer = KafkaConsumer(
        KAFKA_CONTROL_TOPIC,
        bootstrap_servers='localhost:9092',
        auto_offset_reset='latest',
        enable_auto_commit=True,
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        group_id='energy-monitor-control',
    )
    KAFKA_ENABLED = True
    print("✅ Kafka Producer connected on localhost:9092")
except Exception as e:
    producer = None
    control_consumer = None
    KAFKA_ENABLED = False
    print(f"⚠️  Kafka not available ({e}) — CSV-only mode")

# --- Configuration ---
DEFAULT_PORT = os.getenv("ESP32_PORT", "COM9")
BAUD = 115200
PRICE_PER_UNIT = 8.0 
ALPHA = 0.3          # Python-side smoothing
SECONDS_PER_DAY = 24 * 3600

# Day timing: each simulated day lasts 2 real minutes.
DEMO_MODE = True

print("="*65)
mode_label = "⚠️  DEMO (TIME-ACCELERATED)" if DEMO_MODE else "🚀 REAL-TIME MONITORING"
print(f"SYSTEM MODE: {mode_label}")
print(f"DAY LENGTH:  {DAY_LENGTH_SECONDS/60:.0f} minutes")
print(f"TIME SCALE:  {TIME_SCALE:.0f}x")
print("="*65)


def get_available_serial_ports():
    return [p.device for p in list_ports.comports()]


available_ports = get_available_serial_ports()
if available_ports:
    print(f"🔌 Detected serial ports: {', '.join(available_ports)}")
else:
    print("🔌 No serial ports detected right now.")

if DEFAULT_PORT not in available_ports:
    print(f"⚠️ Default port {DEFAULT_PORT} not detected.")

port_input = input(f"Enter ESP32 COM port [{DEFAULT_PORT}]: ").strip()
PORT = port_input or DEFAULT_PORT

def open_serial():
    s = serial.Serial(PORT, BAUD, timeout=1)
    time.sleep(2)           
    s.reset_input_buffer()  # Flush mid-stream garbage
    return s

try:
    ser = open_serial()
except Exception as e:
    print(f"⚠️ Port Error on {PORT}: {e}")
    if available_ports:
        print(f"   Available ports: {', '.join(available_ports)}")
    ser = None

# --- Dashboard-driven budget state ---
monthly_budget = DEFAULT_MONTHLY_BUDGET
daily_budget_base = monthly_budget / 30.0
budget_rollover = 0.0
current_day_budget = daily_budget_base


def build_daily_summary(day_number, day_spent, daily_budget_value, carry_over):
    balance_left = daily_budget_value - day_spent
    day_units = day_spent / PRICE_PER_UNIT if PRICE_PER_UNIT > 0 else 0.0
    avg_power_day = (day_units * 1000.0) / (DAY_LENGTH_SECONDS / 3600.0) if DAY_LENGTH_SECONDS > 0 else 0.0
    safe_avg_power = ((daily_budget_value / PRICE_PER_UNIT) * 1000.0) / (DAY_LENGTH_SECONDS / 3600.0) if PRICE_PER_UNIT > 0 else 0.0

    if balance_left >= 0:
        status = "within_budget"
        tip = (
            f"Keep daily average power at or below {safe_avg_power:.2f} W. "
            "Use high-watt appliances in shorter bursts and turn off idle loads."
        )
        over_budget = 0.0
        reduction_pct = 0.0
    else:
        over_budget = abs(balance_left)
        reduction_pct = (over_budget / daily_budget_value) * 100 if daily_budget_value > 0 else 0.0
        status = "over_budget"
        tip = (
            f"Reduce tomorrow's average load to {safe_avg_power:.2f} W or less. "
            f"Target at least {reduction_pct:.1f}% lower daily usage than today."
        )

    return {
        "day_number": int(day_number),
        "spent": float(day_spent),
        "daily_budget": float(daily_budget_value),
        "carry_over": float(carry_over),
        "balance_left": float(balance_left),
        "over_budget": float(over_budget),
        "avg_power_w": float(avg_power_day),
        "safe_avg_power_w": float(safe_avg_power),
        "status": status,
        "reduction_pct": float(reduction_pct),
        "tip": tip,
    }


def print_daily_summary(summary):
    day_number = summary["day_number"]
    day_spent = summary["spent"]
    daily_budget_value = summary["daily_budget"]
    balance_left = summary["balance_left"]
    avg_power_day = summary["avg_power_w"]

    print("\n")
    print("=" * 64)
    print(f"📅 END OF SIMULATED DAY {day_number}")
    print(f"💸 Budget Used Today : ₹{day_spent:.4f}")
    print(f"🎯 Daily Budget      : ₹{daily_budget_value:.2f}")
    print(f"🔁 Carry Over        : ₹{summary['carry_over']:.4f}")
    print(f"⚡ Avg Power Today   : {avg_power_day:.2f} W")

    if balance_left >= 0:
        print(f"✅ Balance Left      : ₹{balance_left:.4f}")
        print("🧠 Smart Usage Tip   :")
        print(f"   {summary['tip']}")
    else:
        overshoot = summary["over_budget"]
        print(f"🔴 Over Budget By    : ₹{overshoot:.4f}")
        print("🧠 Smart Usage Tip   :")
        print(f"   {summary['tip']}")
    print("=" * 64)


def send_serial_command(command, expected_acks, retries=5, ack_timeout=3.0):
    if ser is None or not ser.is_open:
        return False

    if isinstance(expected_acks, str):
        expected_acks = [expected_acks]

    payload = f"{command}\n".encode("utf-8")
    for _ in range(retries):
        try:
            ser.write(payload)
            ser.flush()
        except Exception:
            continue

        deadline = time.time() + ack_timeout
        while time.time() < deadline:
            line = ser.readline().decode(errors="ignore").strip()
            if any(token in line for token in expected_acks):
                return True

    return False


def apply_control_message(message):
    global monthly_budget, daily_budget_base, budget_rollover, current_day_budget, relay_is_on, firmware_control_capable

    message_type = message.get("message_type", "control")
    if message_type == "config":
        new_monthly_budget = float(message.get("monthly_budget", monthly_budget))
        new_day_length = float(message.get("day_length_seconds", DAY_LENGTH_SECONDS))

        monthly_budget = max(new_monthly_budget, 1.0)
        daily_budget_base = monthly_budget / 30.0
        current_day_budget = daily_budget_base + budget_rollover
        print(f"\n⚙️ Config updated from dashboard: monthly budget ₹{monthly_budget:.2f}, day length {new_day_length:.0f}s")
        return

    command = str(message.get("command", "")).upper()
    if command == "ON":
        ok = send_serial_command("ON", ["CMD_ACK:ON", "RELAY_STATE:ON"])
        if ok:
            relay_is_on = True
            print("\n✅ Dashboard command received: fan ON (ACK)")
        else:
            print("\n⚠️ Dashboard ON command sent, but ACK not received. Reflash ESP32 firmware and retry.")
    elif command == "OFF":
        ok = send_serial_command("OFF", ["CMD_ACK:OFF", "RELAY_STATE:OFF"])

        # Fallback: infer OFF if measured power drops near zero shortly after command.
        if not ok:
            fallback_deadline = time.time() + 4.0
            while time.time() < fallback_deadline:
                line = ser.readline().decode(errors="ignore").strip() if ser and ser.is_open else ""
                match_p = re.search(r"P:([0-9]*\.?[0-9]+)", line)
                if match_p and float(match_p.group(1)) < 0.05:
                    ok = True
                    break

        if ok:
            relay_is_on = False
            print("\n🛑 Dashboard command received: fan OFF (ACK)")
        else:
            if firmware_control_capable:
                print("\n⚠️ Dashboard OFF command sent, but no OFF ACK was received. Check relay polarity (RELAY_ACTIVE_HIGH).")
            else:
                print("\n⚠️ Dashboard OFF command sent, but firmware did not expose command ACK lines. Reflash latest current_calculation.ino.")
    elif command == "CONTINUE":
        print("\n✅ Dashboard decision: continue running")


def poll_control_messages():
    if control_consumer is None:
        return

    try:
        batches = control_consumer.poll(timeout_ms=100, max_records=20)
    except Exception as e:
        print(f"\n⚠️ Control poll failed: {e}")
        return

    for _, messages in batches.items():
        for message in messages:
            apply_control_message(message.value or {})

# --- State ---
total_units = 0.0
total_cost = 0.0
prev_time = None
smoothed_power = None
session_elapsed_s = 0.0
valid_count = 0          
last_debug = time.time() 
alerted = False
fan_off = False   # tracks if we turned the fan off
day_number = 1
day_elapsed_s = 0.0
day_spent = 0.0
budget_alerted_today = False
relay_is_on = False
current_day_budget = daily_budget_base + budget_rollover
current_day_start_budget = current_day_budget
last_day_summary = None
firmware_control_capable = False

# --- CSV --- Always start fresh so dashboard shows current session only
log_file = open("energy_log.csv", "w", newline="")
writer = csv.writer(log_file)
writer.writerow(["real_ts", "sim_elapsed_s", "ampere", "watt", "kwh", "cost_rs"])
log_file.flush()
print("📁 Log file reset — starting fresh session.")

print(f"\n💰 Base Daily Budget: ₹{daily_budget_base:.2f}")
print(f"🔁 Carry Over Budget : ₹{budget_rollover:.2f}")
print(f"🎯 Today's Budget    : ₹{current_day_budget:.2f}")
print("📡 System syncing with ESP32... Use the dashboard to turn fan ON/OFF.")

while True:
    try:
        poll_control_messages()

        if ser is None or not ser.is_open:
            ser = open_serial()

        line = ser.readline().decode(errors="ignore").strip()
    

        # Alive Indicator
        if valid_count < 3 and (time.time() - last_debug > 1.5):
            print("\r⏳ Syncing packets... [Buffer Flush Active]", end="", flush=True)
            last_debug = time.time()

        if not line:
            continue

        # Soft-Start Sync Logic
        if "SYSTEM_STARTUP" in line:
            print("\n🔄 ESP32 Reboot detected. Calibrating...")
            continue
        if "SYSTEM_READY" in line:
            print("\n✅ ESP32 Synced. Data flow starting...")
            continue

        if line.startswith("CMD_") or line.startswith("RELAY_STATE:"):
            firmware_control_capable = True
            print(f"\n🔧 ESP32 control log: {line}")
            continue

        if "ERROR:" in line:
            print(f"\r⚠️ HARDWARE ALERT: {line}          ", end="", flush=True)
            continue

        if "P:" not in line:
            continue

        # Stabilization
        valid_count += 1
        if valid_count <= 3:
            if valid_count == 3:
                print("\n📈 Dashboard Live:\n")
            continue

        # Parsing
        match_i = re.search(r"I:([0-9]*\.?[0-9]+)", line)
        match_p = re.search(r"P:([0-9]*\.?[0-9]+)", line)
        if not match_p:
            continue

        raw_power = float(match_p.group(1))
        current = float(match_i.group(1)) if match_i else 0.0

        # Math & Smoothing
        now = time.time()
        smoothed_power = (ALPHA * raw_power) + ((1 - ALPHA) * (smoothed_power or raw_power))
        
        if prev_time is None:
            prev_time = now
            continue

        real_dt = now - prev_time
        if real_dt <= 0 or real_dt > 5: # Guard
            prev_time = now 
            continue
            
        prev_time = now

        # Real-time accounting for a 2-minute simulated day.
        dt = real_dt
        session_elapsed_s += dt
        day_elapsed_s += dt

        # Scale real elapsed time so one day completes in 2 minutes.
        effective_dt = dt * TIME_SCALE

        # Physics-Preserved Calculations
        units = (smoothed_power / 1000.0) * (effective_dt / 3600.0)
        total_units += units
        delta_cost = units * PRICE_PER_UNIT
        total_cost += delta_cost
        cost_per_hour = (smoothed_power / 1000.0) * PRICE_PER_UNIT

        day_spent += delta_cost

        # End-of-day accounting in real time.
        while day_elapsed_s >= DAY_LENGTH_SECONDS:
            day_summary = build_daily_summary(day_number, day_spent, current_day_start_budget, budget_rollover)
            print_daily_summary(day_summary)

            if KAFKA_ENABLED and producer is not None:
                summary_payload = {
                    "message_type": "daily_summary",
                    "timestamp": now,
                    "monthly_budget": monthly_budget,
                    "day_length_seconds": DAY_LENGTH_SECONDS,
                    **day_summary,
                }
                try:
                    producer.send(KAFKA_TOPIC, summary_payload)
                except Exception as kafka_summary_error:
                    print(f"\n⚠️ Kafka day-summary publish failed: {kafka_summary_error}")

            budget_rollover = current_day_start_budget - day_spent
            day_number += 1
            day_elapsed_s -= DAY_LENGTH_SECONDS
            day_spent = 0.0
            budget_alerted_today = False
            current_day_start_budget = daily_budget_base + budget_rollover
            current_day_budget = current_day_start_budget
            print(f"🎯 Day {day_number} budget set to ₹{current_day_budget:.2f} (rollover ₹{budget_rollover:.2f})")

        # Simulated Projection (Normalizing to a 24h simulated day)
        proj_24h_sim = cost_per_hour * 24

        remaining = max(current_day_budget - day_spent, 0)
        hours_to_limit = (remaining / cost_per_hour) if cost_per_hour > 0 else float('inf')

        # Log Data
        writer.writerow([now, session_elapsed_s, current, smoothed_power, total_units, total_cost])
        log_file.flush()

        # Publish live reading to Kafka for dashboard streaming
        if KAFKA_ENABLED and producer is not None:
            payload = {
                "message_type": "reading",
                "timestamp": now,
                "watt": smoothed_power,
                "cost_rs": total_cost,
                "kwh": total_units,
                "ampere": current,
                "day_number": day_number,
                "day_elapsed_s": day_elapsed_s,
                "day_spent": day_spent,
                "day_budget": current_day_budget,
                "budget_rollover": budget_rollover,
                "relay_state": "ON" if relay_is_on else "OFF",
                "monthly_budget": monthly_budget,
                "daily_budget": current_day_budget,
            }
            try:
                producer.send(KAFKA_TOPIC, payload)
            except Exception as kafka_send_error:
                print(f"\n⚠️ Kafka publish failed: {kafka_send_error}")

        if day_spent >= current_day_budget and not budget_alerted_today:
            budget_alerted_today = True
            if KAFKA_ENABLED and producer is not None:
                alert_payload = {
                    "message_type": "budget_alert",
                    "timestamp": now,
                    "day_number": day_number,
                    "day_spent": day_spent,
                    "day_budget": current_day_budget,
                    "balance_left": current_day_budget - day_spent,
                    "relay_state": "ON" if relay_is_on else "OFF",
                    "suggested_action": "turn_off",
                }
                try:
                    producer.send(KAFKA_TOPIC, alert_payload)
                except Exception:
                    pass

        # Status & Alerts
        status = "ON " if smoothed_power > 0.05 else "OFF"

        if day_spent >= current_day_budget:
            eta_display = "DONE"
        elif cost_per_hour > 0:
            eta_display = f"{hours_to_limit:5.1f}h"
        else:
            eta_display = "∞"
        
        alert = ""
        if day_spent >= current_day_budget:
            alert = " | 🔴 LIMIT EXCEEDED"
        elif hours_to_limit < 1:
            alert = " | 🔴 CRITICAL ETA"
        elif hours_to_limit < 5:
            alert = " | 🟡 WARNING"

        # Final Dashboard Render
        print(f"\r⚡ {smoothed_power:>5.2f}W ({status}) | 💸 Day ₹{day_spent:.4f}/{current_day_budget:.2f} | 🔮 Proj/24h: ₹{proj_24h_sim:>6.2f} | ⏳ ETA: {eta_display}{alert}          ", end="", flush=True)

    except KeyboardInterrupt:
        print("\n\n🛑 Shutdown initiated.")
        log_file.flush()
        log_file.close()

        if ser and ser.is_open:
            ser.close()
        if producer is not None:
            try:
                producer.flush(timeout=2)
                producer.close()
            except Exception:
                pass

        sim_hours = session_elapsed_s / 3600

        print("\n📊 SESSION SUMMARY")
        print("====================================")
        print(f"⚡ Total Units Used: {total_units:.6f} kWh")
        print(f"💰 Total Cost: ₹{total_cost:.4f}")
        print(f"⏱️ Simulated Time: {sim_hours:.2f} hours")
        print(f"📈 Avg Power: {(total_units*1000)/(session_elapsed_s/3600) if session_elapsed_s>0 else 0:.2f} W")
        print("====================================\n")
        break

    except Exception:
        continue