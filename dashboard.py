import json
import time

import numpy as np
import pandas as pd
import streamlit as st
from kafka import KafkaConsumer, KafkaProducer
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import HuberRegressor
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import LabelEncoder
from sklearn.tree import DecisionTreeClassifier

st.set_page_config(page_title="Smart Energy Dashboard", layout="wide", page_icon="⚡")

st.markdown("""
<style>
/* App Background */
.stApp {
    background: radial-gradient(circle at top left, #0b1320, #000000);
}

/* Gradient Headings */
h1, h2, h3 {
    background: -webkit-linear-gradient(45deg, #4facfe 0%, #00f2fe 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-weight: 800;
}

/* Glassmorphism Metrics */
[data-testid="stMetric"] {
    background: rgba(20, 35, 55, 0.4);
    border: 1px solid rgba(129, 190, 255, 0.15);
    backdrop-filter: blur(10px);
    border-radius: 12px;
    padding: 15px 20px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
    transition: transform 0.2s ease, box-shadow 0.2s ease;
}
[data-testid="stMetric"]:hover {
    transform: translateY(-3px);
    box-shadow: 0 8px 25px rgba(0, 150, 255, 0.15);
    border: 1px solid rgba(129, 190, 255, 0.4);
}

/* Metric Value and Label Styling */
[data-testid="stMetricValue"] {
    font-size: 2rem !important;
    font-weight: 800 !important;
    color: #ffffff !important;
}
[data-testid="stMetricLabel"] {
    font-size: 0.85rem !important;
    font-weight: 600 !important;
    color: #8bb3d4 !important;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}

/* Tabs Styling */
[data-baseweb="tab-list"] {
    background: rgba(20, 35, 55, 0.4);
    border-radius: 10px;
    padding: 5px;
    gap: 5px;
}
[data-baseweb="tab"] {
    border-radius: 8px !important;
    padding: 10px 20px !important;
    transition: all 0.3s ease !important;
    color: #8bb3d4 !important;
    border: none !important;
    background: transparent !important;
}
[aria-selected="true"] {
    background: rgba(79, 172, 254, 0.15) !important;
    color: #00f2fe !important;
    box-shadow: inset 0 0 0 1px rgba(0, 242, 254, 0.5);
}

/* Primary Button Styling */
button[kind="primary"] {
    background: linear-gradient(90deg, #00C9FF 0%, #92FE9D 100%) !important;
    color: #000 !important;
    font-weight: bold !important;
    border: none !important;
    border-radius: 8px !important;
    box-shadow: 0 4px 15px rgba(0, 201, 255, 0.4) !important;
    transition: transform 0.2s, box-shadow 0.2s !important;
}
button[kind="primary"]:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 6px 20px rgba(0, 201, 255, 0.6) !important;
}

/* Containers / Cards */
[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 16px !important;
    background: rgba(20, 35, 55, 0.3) !important;
    border: 1px solid rgba(129, 190, 255, 0.12) !important;
    backdrop-filter: blur(12px) !important;
    transition: transform 0.2s, border 0.2s !important;
}
[data-testid="stVerticalBlockBorderWrapper"]:hover {
    border: 1px solid rgba(129, 190, 255, 0.35) !important;
    transform: translateY(-2px);
}

/* Sidebar */
[data-testid="stSidebar"] {
    background: rgba(8, 15, 25, 0.95) !important;
    border-right: 1px solid rgba(129, 190, 255, 0.1) !important;
}
</style>
""", unsafe_allow_html=True)

KAFKA_TOPIC = "energy-data"
KAFKA_CONTROL_TOPIC = "energy-control"
KAFKA_BOOTSTRAP = "localhost:9092"
MAX_POINTS = 5000
DAY_LENGTH_MINUTES_DEFAULT = 2.0


@st.cache_resource
def get_global_state():
    return {
        "kafka_rows": [],
        "monthly_history_rows": [],
        "daily_summaries": [],
        "latest_budget_alert": None,
    }

gs = get_global_state()
st.session_state.kafka_rows = gs["kafka_rows"]
st.session_state.monthly_history_rows = gs["monthly_history_rows"]
st.session_state.daily_summaries = gs["daily_summaries"]

if "kafka_status" not in st.session_state:
    st.session_state.kafka_status = "connecting"
if "kafka_error" not in st.session_state:
    st.session_state.kafka_error = ""

if "latest_budget_alert" not in st.session_state:
    st.session_state.latest_budget_alert = gs["latest_budget_alert"]
else:
    gs["latest_budget_alert"] = st.session_state.latest_budget_alert

if "producer_monthly_budget" not in st.session_state:
    st.session_state.producer_monthly_budget = None
if "budget_user_override" not in st.session_state:
    st.session_state.budget_user_override = False
if "monthly_budget_input" not in st.session_state:
    st.session_state.monthly_budget_input = 300.0
if "last_sent_config" not in st.session_state:
    st.session_state.last_sent_config = None


@st.cache_resource
def get_kafka_producer():
    return KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )


@st.cache_resource
def get_kafka_consumer():
    return KafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        auto_offset_reset="latest",
        enable_auto_commit=True,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        group_id="energy-dashboard-live",
    )


def poll_kafka_messages(max_records=200):
    try:
        consumer = get_kafka_consumer()
        batches = consumer.poll(timeout_ms=250, max_records=max_records)
        received = 0
        for _, messages in batches.items():
            for message in messages:
                data = message.value or {}
                message_type = data.get("message_type", "reading")

                if message_type == "daily_summary":
                    summary = {
                        "day_number": int(data.get("day_number", 0)),
                        "spent": float(data.get("spent", 0.0)),
                        "daily_budget": float(data.get("daily_budget", 0.0)),
                        "balance_left": float(data.get("balance_left", 0.0)),
                        "over_budget": float(data.get("over_budget", 0.0)),
                        "avg_power_w": float(data.get("avg_power_w", 0.0)),
                        "safe_avg_power_w": float(data.get("safe_avg_power_w", 0.0)),
                        "status": str(data.get("status", "within_budget")),
                        "reduction_pct": float(data.get("reduction_pct", 0.0)),
                        "tip": str(data.get("tip", "")),
                        "timestamp": float(data.get("timestamp", time.time())),
                    }
                    st.session_state.daily_summaries.append(summary)
                    if len(st.session_state.daily_summaries) > 120:
                        st.session_state.daily_summaries = st.session_state.daily_summaries[-120:]

                    mb = float(data.get("monthly_budget", 0.0))
                    if mb > 0:
                        st.session_state.producer_monthly_budget = mb
                        if not st.session_state.budget_user_override:
                            st.session_state.monthly_budget_input = mb
                    received += 1
                    continue

                if message_type == "budget_alert":
                    st.session_state.latest_budget_alert = {
                        "day_number": int(data.get("day_number", 0)),
                        "day_spent": float(data.get("day_spent", 0.0)),
                        "day_budget": float(data.get("day_budget", 0.0)),
                        "balance_left": float(data.get("balance_left", 0.0)),
                        "relay_state": str(data.get("relay_state", "OFF")),
                        "suggested_action": str(data.get("suggested_action", "turn_off")),
                        "timestamp": float(data.get("timestamp", time.time())),
                    }
                    received += 1
                    continue

                row = {
                    "real_ts": float(data.get("timestamp", time.time())),
                    "sim_elapsed_s": float(data.get("sim_elapsed_s", 0.0)),
                    "ampere": float(data.get("ampere", 0.0)),
                    "watt": float(data.get("watt", 0.0)),
                    "kwh": float(data.get("kwh", 0.0)),
                    "cost_rs": float(data.get("cost_rs", 0.0)),
                    "monthly_budget": float(data.get("monthly_budget", 0.0)),
                    "daily_budget": float(data.get("daily_budget", 0.0)),
                    "day_number": int(data.get("day_number", 0)),
                    "day_elapsed_s": float(data.get("day_elapsed_s", 0.0)),
                    "day_spent": float(data.get("day_spent", 0.0)),
                    "budget_rollover": float(data.get("budget_rollover", 0.0)),
                    "relay_state": str(data.get("relay_state", "OFF")),
                }
                
                # Detect if energy_monitor.py was restarted (kwh or day drops)
                if st.session_state.kafka_rows:
                    last_kwh = st.session_state.kafka_rows[-1]["kwh"]
                    last_day = st.session_state.kafka_rows[-1]["day_number"]
                    if row["kwh"] < last_kwh or row["day_number"] < last_day:
                        st.session_state.kafka_rows.clear()
                        st.session_state.monthly_history_rows.clear()
                        st.session_state.daily_summaries.clear()
                        st.session_state.latest_budget_alert = None
                        get_global_state()["latest_budget_alert"] = None
                
                st.session_state.kafka_rows.append(row)
                st.session_state.monthly_history_rows.append(row)
                if row["monthly_budget"] > 0:
                    st.session_state.producer_monthly_budget = row["monthly_budget"]
                    if not st.session_state.budget_user_override:
                        st.session_state.monthly_budget_input = row["monthly_budget"]
                received += 1

        if len(st.session_state.kafka_rows) > MAX_POINTS:
            st.session_state.kafka_rows = st.session_state.kafka_rows[-MAX_POINTS:]
        if len(st.session_state.monthly_history_rows) > MAX_POINTS:
            st.session_state.monthly_history_rows = st.session_state.monthly_history_rows[-MAX_POINTS:]

        # Reaching poll() successfully means broker is reachable.
        st.session_state.kafka_status = "connected"
        st.session_state.kafka_error = ""
        return received
    except Exception as e:
        st.session_state.kafka_status = "error"
        st.session_state.kafka_error = str(e)
        return 0


def send_control_message(command, extra=None):
    try:
        producer = get_kafka_producer()
        payload = {
            "message_type": "control",
            "command": command,
            "timestamp": time.time(),
        }
        if extra:
            payload.update(extra)
        producer.send(KAFKA_CONTROL_TOPIC, payload)
        producer.flush(timeout=2)
        return True
    except Exception as e:
        st.session_state.kafka_error = str(e)
        return False


def sync_device_config(monthly_budget):
    config_key = (round(float(monthly_budget), 2), float(DAY_LENGTH_MINUTES_DEFAULT))
    if st.session_state.last_sent_config == config_key:
        return

    success = send_control_message(
        "SET_CONFIG",
        {
            "message_type": "config",
            "monthly_budget": float(monthly_budget),
            "day_length_seconds": float(DAY_LENGTH_MINUTES_DEFAULT) * 60.0,
        },
    )
    if success:
        st.session_state.last_sent_config = config_key


def load_stream_data():
    rows = list(st.session_state.kafka_rows)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).dropna()
    if df.empty or "cost_rs" not in df.columns:
        return pd.DataFrame()
    df["real_ts"] = pd.to_datetime(df["real_ts"], unit="s")
    return df


def build_daily_summary_df():
    if not st.session_state.daily_summaries:
        return pd.DataFrame()
    out = pd.DataFrame(st.session_state.daily_summaries).copy()
    for col, default in {
        "day_number": 0,
        "spent": 0.0,
        "daily_budget": 0.0,
        "carry_over": 0.0,
        "balance_left": 0.0,
        "status": "within_budget",
    }.items():
        if col not in out.columns:
            out[col] = default
    return out.sort_values("day_number").reset_index(drop=True)


def robust_day_spend_forecast(dm, day_length_seconds):
    work = dm.copy()
    X = work[["elapsed_s"]].values
    y = work["day_spent"].values

    if len(work) < 6 or np.allclose(X.std(), 0):
        rate = max((y[-1] - y[0]) / max((X[-1][0] - X[0][0]), 1e-6), 0.0)
        pred_end = max(y[-1], y[-1] + rate * max(day_length_seconds - X[-1][0], 0.0))
        return rate, pred_end, max(pred_end * 0.9, 0.0), pred_end * 1.1

    model = HuberRegressor(epsilon=1.4, alpha=0.0005)
    model.fit(X, y)

    rate = max(float(model.coef_[0]), 0.0)
    pred_curve = model.predict(X)
    pred_end = max(float(model.predict(np.array([[day_length_seconds]]))[0]), y[-1])
    resid_std = float(np.std(y - pred_curve)) if len(y) > 2 else 0.0
    return rate, pred_end, max(pred_end - 1.64 * resid_std, 0.0), pred_end + 1.64 * resid_std


def forecast_month_ensemble(daily_df, monthly_budget):
    if daily_df.empty:
        return None

    actual_sum = float(daily_df["spent"].sum())
    days_done = int(daily_df["day_number"].max())
    days_left = max(30 - days_done, 0)
    mean_spend = float(daily_df["spent"].mean())
    std_spend = float(daily_df["spent"].std() if len(daily_df) > 1 else 0.0)

    ewma_next = float(daily_df["spent"].ewm(span=min(7, len(daily_df)), adjust=False).mean().iloc[-1])
    ewma_month_total = actual_sum + ewma_next * days_left

    if len(daily_df) >= 3:
        trend_model = HuberRegressor(epsilon=1.5, alpha=0.0005)
        trend_model.fit(daily_df[["day_number"]].values, daily_df["spent"].values)
        future_days = np.arange(days_done + 1, 31).reshape(-1, 1)
        trend_future = np.maximum(trend_model.predict(future_days), 0.0)
        trend_month_total = actual_sum + float(trend_future.sum())
    else:
        trend_month_total = actual_sum + mean_spend * days_left

    ensemble_total = 0.6 * ewma_month_total + 0.4 * trend_month_total

    rng = np.random.default_rng(42)
    if days_left > 0 and std_spend > 0:
        sims = rng.normal(loc=max(ewma_next, 0.0), scale=std_spend, size=(700, days_left))
        sims = np.clip(sims, 0.0, None)
        sim_totals = actual_sum + sims.sum(axis=1)
        overrun_prob = float((sim_totals > monthly_budget).mean())
    else:
        overrun_prob = 1.0 if ensemble_total > monthly_budget else 0.0

    return {
        "days_done": days_done,
        "actual_sum": actual_sum,
        "ewma_month_total": float(ewma_month_total),
        "trend_month_total": float(trend_month_total),
        "ensemble_total": float(ensemble_total),
        "avg_day_spend": mean_spend,
        "overrun_prob": overrun_prob,
        "buffer": float(monthly_budget - ensemble_total),
    }


poll_kafka_messages()
df = load_stream_data()


def on_budget_change():
    st.session_state.budget_user_override = True

sim_time_str = "--:--"
current_day_str = "Day -"
if not df.empty and "day_elapsed_s" in df.columns:
    elapsed_s = float(df.iloc[-1]["day_elapsed_s"])
    day_num = int(df.iloc[-1].get("day_number", 1))
    current_day_str = f"Day {day_num}"
    
    # 2 minutes (120s) real time = 24 hours sim time
    sim_hours = (elapsed_s / (DAY_LENGTH_MINUTES_DEFAULT * 60.0)) * 24.0
    h = int(sim_hours)
    m = int((sim_hours - h) * 60)
    
    period = "AM"
    display_h = h
    if h >= 12:
        period = "PM"
        if h > 12:
            display_h = h - 12
    if display_h == 0:
        display_h = 12
    sim_time_str = f"{display_h:02d}:{m:02d} {period}"

header_col1, header_col2 = st.columns([3, 1])
with header_col1:
    st.title("⚡ Smart Energy Monitoring Dashboard")
with header_col2:
    st.write("<br>", unsafe_allow_html=True)
    st.markdown(f"<h3 style='text-align: right; color: #64B5F6; margin-bottom: 0;'>🕒 {sim_time_str}</h3>", unsafe_allow_html=True)
    st.markdown(f"<p style='text-align: right; font-size: 0.85rem; opacity: 0.7; margin-top: 0;'>{current_day_str} &nbsp;|&nbsp; Updated: {pd.Timestamp.now().strftime('%H:%M:%S')}</p>", unsafe_allow_html=True)
if st.session_state.kafka_status == "connected":
    st.success(f"Kafka connected: {KAFKA_BOOTSTRAP} | topics: {KAFKA_TOPIC}, {KAFKA_CONTROL_TOPIC}")
elif st.session_state.kafka_status == "error":
    st.error(f"Kafka error: {st.session_state.kafka_error}")
else:
    st.info("Connecting to Kafka...")

latest_reading = df.iloc[-1] if not df.empty else None
latest_day = st.session_state.daily_summaries[-1] if st.session_state.daily_summaries else None

current_day_spent = float(latest_reading["day_spent"]) if latest_reading is not None and "day_spent" in latest_reading else (float(latest_day["spent"]) if latest_day else 0.0)
current_day_budget = float(latest_reading["daily_budget"]) if latest_reading is not None and "daily_budget" in latest_reading else (float(latest_day["daily_budget"]) if latest_day else daily_limit)
current_day_number = int(latest_reading["day_number"]) if latest_reading is not None and "day_number" in latest_reading else (int(latest_day["day_number"]) if latest_day else 1)
current_relay_state = str(latest_reading["relay_state"]) if latest_reading is not None and "relay_state" in latest_reading else (str(st.session_state.latest_budget_alert["relay_state"]) if st.session_state.latest_budget_alert else "OFF")
current_balance = current_day_budget - current_day_spent

st.markdown("<hr style='margin-top: 10px; margin-bottom: 20px;'>", unsafe_allow_html=True)
fc_c1, fc_c2 = st.columns([2, 1])

with fc_c1:
    if current_day_spent >= current_day_budget:
        st.error(f"🔴 **Day {current_day_number} Budget Exceeded!** Spent ₹{current_day_spent:.4f} of ₹{current_day_budget:.2f}.")
    elif current_day_budget > 0:
        usage_pct = min((current_day_spent / current_day_budget) * 100, 100)
        if usage_pct >= 80:
            st.warning(f"🟡 **Warning:** Day {current_day_number} is at {usage_pct:.1f}% capacity. Balance: ₹{current_balance:.4f}")
        else:
            st.success(f"🟢 **Safe:** Day {current_day_number} is within budget. Balance: ₹{current_balance:.4f} remains.")

with fc_c2:
    st.markdown("**🎛️ Fan Control**")
    b1, b2 = st.columns(2)
    if b1.button("✅ Turn ON", use_container_width=True, type="primary"):
        send_control_message("ON")
        st.toast("Sent ON command")
    
    if current_day_spent >= current_day_budget:
        if b2.button("🛑 STOP FAN", use_container_width=True):
            send_control_message("OFF")
            st.toast("Sent OFF command")
        if st.button("⚠️ Keep Running", use_container_width=True):
            send_control_message("CONTINUE")
            st.toast("Acknowledged - Keeping Fan ON")
    else:
        if b2.button("❌ Turn OFF", use_container_width=True):
            send_control_message("OFF")
            st.toast("Sent OFF command")
st.markdown("<hr style='margin-top: 5px; margin-bottom: 20px;'>", unsafe_allow_html=True)

with st.sidebar:
    st.header("📍 Monthly Budget")
    monthly_budget = st.number_input(
        "Budget for this month (₹)",
        min_value=1.0,
        step=1.0,
        key="monthly_budget_input",
        on_change=on_budget_change,
    )
    daily_limit = monthly_budget / 30.0
    st.metric("Daily Budget", f"₹{daily_limit:.2f}")
    st.caption("Daily budget is derived automatically from the monthly budget.")
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("Apply Budget Config", use_container_width=True):
        sync_device_config(monthly_budget)
        st.toast("✅ Budget Configuration Sent!")

# Removed global sync_device_config call to let the sidebar button handle it.

tab1, tab2, tab3 = st.tabs(["📡 Live Monitor", "📅 Month View & Insights", "🔮 ML Predictions"])

month_start = pd.Timestamp.today().normalize()

with tab1:
    if df.empty:
        st.warning("⏳ Waiting for Kafka readings... turn the fan ON from the dashboard to begin.")
    else:
        latest = df.iloc[-1]
        cur_cost = float(latest["day_spent"] if "day_spent" in latest else latest["cost_rs"])
        cur_watt = float(latest["watt"])
        cur_day_budget = float(latest["daily_budget"] if "daily_budget" in latest else daily_limit)
        remaining = max(cur_day_budget - cur_cost, 0)
        pct_used = min((cur_cost / cur_day_budget) * 100, 100) if cur_day_budget > 0 else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("⚡ Power", f"{cur_watt:.2f} W")
        c2.metric("💰 Day Spent", f"₹{cur_cost:.4f}")
        c3.metric("🏦 Remaining", f"₹{remaining:.4f}")
        c4.metric("🔋 Relay", current_relay_state)

        if st.session_state.latest_budget_alert:
            alert = st.session_state.latest_budget_alert
            if alert["day_number"] == int(latest.get("day_number", 1)):
                st.error(
                    f"🔴 Budget crossed on Day {alert['day_number']}: spent ₹{cur_cost:.4f} of ₹{cur_day_budget:.2f}. "
                    f"Recommended action: {alert['suggested_action'].replace('_', ' ')}"
                )

        st.markdown(f"**Budget Used: {pct_used:.1f}%**")
        st.progress(pct_used / 100 if pct_used <= 100 else 1.0)

        st.divider()
        st.subheader("📅 Day Summary")
        if latest_day:
            d1, d2, d3, d4 = st.columns(4)
            d1.metric("Day", f"{latest_day['day_number']}")
            d2.metric("Spent", f"₹{latest_day['spent']:.4f}")
            if latest_day["status"] == "over_budget":
                d3.metric("Over Budget", f"₹{latest_day['over_budget']:.4f}")
            else:
                d3.metric("Balance Left", f"₹{max(latest_day['balance_left'], 0):.4f}")
            d4.metric("Safe Avg Power", f"{latest_day['safe_avg_power_w']:.2f} W")

            if latest_day["status"] == "over_budget":
                st.error(f"🔴 Day {latest_day['day_number']} exceeded budget. {latest_day['tip']}")
            else:
                st.success(f"✅ Day {latest_day['day_number']} stayed in budget. {latest_day['tip']}")

            history_df = pd.DataFrame(st.session_state.daily_summaries)
            for col, default in {
                "carry_over": 0.0,
                "daily_budget": 0.0,
                "balance_left": 0.0,
                "over_budget": 0.0,
                "avg_power_w": 0.0,
                "status": "within_budget",
            }.items():
                if col not in history_df.columns:
                    history_df[col] = default
            history_df["date"] = month_start + pd.to_timedelta(history_df["day_number"] - 1, unit="D")
            st.dataframe(
                history_df[[
                    "day_number",
                    "date",
                    "spent",
                    "daily_budget",
                    "carry_over",
                    "balance_left",
                    "over_budget",
                    "avg_power_w",
                    "status",
                ]].tail(10),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Daily summary will appear after the first 2-minute day completes.")

        st.divider()
        st.subheader("📈 Power and Spend Over Time")
        st.line_chart(df.set_index("real_ts")["watt"], height=230)
        st.line_chart(df.set_index("real_ts")["day_spent"], height=230)

with tab2:
    st.subheader("📆 Monthly View")
    month_name = month_start.strftime("%B %Y")
    st.caption(f"Simulation month starting from today: {month_name}")

    if st.session_state.daily_summaries:
        month_df = pd.DataFrame(st.session_state.daily_summaries).copy()
        for col, default in {
            "carry_over": 0.0,
            "daily_budget": 0.0,
            "balance_left": 0.0,
            "status": "within_budget",
        }.items():
            if col not in month_df.columns:
                month_df[col] = default
        month_df["date"] = month_start + pd.to_timedelta(month_df["day_number"] - 1, unit="D")
        month_df["cumulative_spent"] = month_df["spent"].cumsum()
        month_df["remaining_month_budget"] = monthly_budget - month_df["cumulative_spent"]

        c1, c2, c3 = st.columns(3)
        c1.metric("Days Completed", f"{len(month_df)}")
        c2.metric("Spent So Far", f"₹{month_df['cumulative_spent'].iloc[-1]:.2f}")
        c3.metric("Month Balance", f"₹{month_df['remaining_month_budget'].iloc[-1]:.2f}")

        if len(month_df) >= 3:
            trend_x = month_df[["day_number"]]
            trend_y = month_df["cumulative_spent"]
            forecast_model = LinearRegression().fit(trend_x, trend_y)
            forecast_days = pd.DataFrame({"day_number": range(1, 31)})
            forecast_days["forecast_spent"] = forecast_model.predict(forecast_days[["day_number"]])
            forecast_days["forecast_spent"] = forecast_days["forecast_spent"].clip(lower=0)
            forecast_month_total = float(forecast_days["forecast_spent"].iloc[-1])

            st.metric("Forecast Month Spend", f"₹{forecast_month_total:.2f}")
            if forecast_month_total > monthly_budget:
                st.warning(f"Projected month spend is above budget by ₹{forecast_month_total - monthly_budget:.2f}.")
            else:
                st.success(f"Projected month spend stays within budget with ₹{monthly_budget - forecast_month_total:.2f} spare.")

            chart_df = pd.DataFrame({
                "Actual Cumulative": month_df["cumulative_spent"].values,
                "Forecast Cumulative": forecast_days.loc[: len(month_df) - 1, "forecast_spent"].values,
            })
            st.line_chart(chart_df, height=200)
        else:
            st.info("Need at least 3 completed days for the monthly forecast.")

        st.subheader("🧾 Day-by-Day Report")
        st.dataframe(
            month_df[[
                "day_number",
                "date",
                "spent",
                "daily_budget",
                "carry_over",
                "balance_left",
                "status",
            ]],
            use_container_width=True,
            hide_index=True,
        )

        st.divider()
        st.subheader("🔮 Stable Forecasting")
        if len(month_df) >= 3:
            spending_rate = month_df["spent"].mean()
            projected_30_day_spend = spending_rate * 30
            days_left_to_budget = (monthly_budget - month_df["cumulative_spent"].iloc[-1]) / spending_rate if spending_rate > 0 else float("inf")
            a, b, c = st.columns(3)
            a.metric("Avg Daily Spend", f"₹{spending_rate:.2f}")
            b.metric("Projected 30-Day Spend", f"₹{projected_30_day_spend:.2f}")
            c.metric("Days Left at Current Pace", f"{days_left_to_budget:.1f}" if days_left_to_budget != float("inf") else "∞")
        else:
            st.info("The forecast section becomes more accurate after a few completed days.")

        st.divider()
        st.subheader("📊 Current Power Health")
        if df.empty:
            st.info("No live readings yet.")
        else:
            live_df = df.copy().reset_index(drop=True)
            if len(live_df) >= 5:
                p33, p66 = live_df["watt"].quantile(0.33), live_df["watt"].quantile(0.66)
                live_df["risk"] = live_df["watt"].apply(lambda w: "Safe" if w <= p33 else ("Warning" if w <= p66 else "Danger"))
                le = LabelEncoder()
                clf = DecisionTreeClassifier(max_depth=3, random_state=42)
                clf.fit(live_df[["watt", "day_spent"]], le.fit_transform(live_df["risk"]))
                current_risk = le.inverse_transform(clf.predict([[float(live_df.iloc[-1]["watt"]), float(live_df.iloc[-1]["day_spent"])] ]))[0]

                c1, c2 = st.columns([1, 2])
                with c1:
                    {"Safe": st.success, "Warning": st.warning, "Danger": st.error}[current_risk](f"**{current_risk.upper()}**")
                    st.metric("Current Power", f"{float(live_df.iloc[-1]['watt']):.2f} W")
                with c2:
                    st.bar_chart(live_df["risk"].value_counts(), height=160)
            else:
                st.info("Need at least 5 live readings for power-health classification.")

        st.divider()
        st.subheader("💡 What-If Cost Simulator")
        c1, c2 = st.columns(2)
        live_power_default = float(df.iloc[-1]["watt"]) if not df.empty else 50.0
        wi_power = c1.slider("Device Power (W):", 0.1, 2000.0, float(round(live_power_default, 1)), 0.1, key="wip")
        wi_hours = c2.slider("Usage hours per day:", 1, 24, 8, key="wih")
        predicted_daily = (wi_power / 1000.0) * wi_hours * 8.0
        predicted_monthly = predicted_daily * 30
        c1, c2, c3 = st.columns(3)
        c1.metric("📅 Daily Cost", f"₹{predicted_daily:.4f}")
        c2.metric("📆 Monthly Cost", f"₹{predicted_monthly:.2f}")
        risk_lbl = "🟢 Low" if predicted_monthly < 200 else ("🟡 Medium" if predicted_monthly < 600 else "🔴 High")
        c3.metric("⚡ Budget Risk", risk_lbl)
    else:
        st.info("Daily summaries will appear here as each 2-minute day finishes.")

with tab3:
    st.subheader("🔮 ML Predictions")

    if df.empty or len(df) < 8:
        st.info("Need at least 8 live readings to generate ML predictions.")
    else:
        dm = df.copy().reset_index(drop=True)
        dm["elapsed_s"] = (dm["real_ts"] - dm["real_ts"].iloc[0]).dt.total_seconds()
        dm = dm[dm["elapsed_s"] > 0]

        if dm.empty or len(dm) < 5:
            st.info("Need more elapsed data points for predictions.")
        else:
            latest = dm.iloc[-1]
            cur_day_spent = float(latest["day_spent"])
            cur_watt = float(latest["watt"])
            day_length_seconds = DAY_LENGTH_MINUTES_DEFAULT * 60.0

            st.markdown('<div class="ml-card"><h4>Model Stack</h4><p>Robust day forecast uses Huber regression. Monthly projection uses an ensemble of EWMA and robust trend, plus Monte Carlo overrun probability.</p></div>', unsafe_allow_html=True)

            cur_day_budget = float(latest["daily_budget"]) if "daily_budget" in latest else daily_limit

            colA, colB = st.columns(2)

            with colA:
                with st.container(border=True):
                    st.markdown("#### ⏳ 1. Robust Day Forecast")
                    rate_per_sec, pred_end_day, ci_low, ci_high = robust_day_spend_forecast(dm, day_length_seconds)

                    c1, c2 = st.columns(2)
                    c1.metric("Predicted End-of-Day", f"₹{pred_end_day:.4f}")
                    c2.metric("Burn Rate", f"₹{rate_per_sec*60:.5f}/min")
                    st.caption(f"Confidence band (90%): ₹{ci_low:.4f} - ₹{ci_high:.4f}")

                    trend_lr = LinearRegression().fit(dm[["elapsed_s"]], dm["day_spent"])
                    trend_df = pd.DataFrame({
                        "Actual Spend": dm["day_spent"].values,
                        "Baseline Trend": trend_lr.predict(dm[["elapsed_s"]]),
                    })
                    st.line_chart(trend_df, height=150)

                with st.container(border=True):
                    st.markdown("#### 🛡️ 2. Usage Risk Classifier")
                    p33, p66 = dm["watt"].quantile(0.33), dm["watt"].quantile(0.66)
                    dm["risk"] = dm["watt"].apply(lambda w: "Safe" if w <= p33 else ("Warning" if w <= p66 else "Danger"))
                    if dm["risk"].nunique() >= 2:
                        le = LabelEncoder()
                        clf = DecisionTreeClassifier(max_depth=3, random_state=42)
                        clf.fit(dm[["watt", "day_spent"]], le.fit_transform(dm["risk"]))
                        predicted_risk = le.inverse_transform(clf.predict([[cur_watt, cur_day_spent]]))[0]
                        r1, r2 = st.columns([1, 1.5])
                        with r1:
                            {"Safe": st.success, "Warning": st.warning, "Danger": st.error}[predicted_risk](f"**{predicted_risk}**")
                        with r2:
                            st.bar_chart(dm["risk"].value_counts(), height=130)
                    else:
                        st.info("Gathering more varied readings for risk classes...")

                with st.container(border=True):
                    st.markdown("#### 🚨 3. Live Anomaly Detector")
                    feature_df = dm[["watt", "ampere", "day_spent"]].copy().dropna()
                    if len(feature_df) >= 25:
                        iso = IsolationForest(n_estimators=120, contamination=0.08, random_state=42)
                        iso.fit(feature_df.iloc[:-1] if len(feature_df) > 25 else feature_df)
                        point = feature_df.iloc[[-1]]
                        pred = int(iso.predict(point)[0])
                        score = float(iso.decision_function(point)[0])
                        
                        a1, a2 = st.columns(2)
                        a1.metric("Anomaly Score", f"{score:.3f}")
                        a2.metric("Status", "⚠️ Anomaly" if pred == -1 else "✅ Normal")
                    else:
                        mu = dm["watt"].mean()
                        sigma = dm["watt"].std() if dm["watt"].std() > 0 else 0.001
                        z = (cur_watt - mu) / sigma
                        a1, a2 = st.columns(2)
                        a1.metric("Z-Score", f"{z:.2f}")
                        a2.metric("Status", "⚠️ Anomaly" if abs(z) > 2 else "✅ Normal")

            with colB:
                with st.container(border=True):
                    st.markdown("#### ⏱️ 4. Budget Exhaustion Timer")
                    remaining_today = cur_day_budget - cur_day_spent
                    b1, b2, b3 = st.columns(3)
                    b1.metric("Budget", f"₹{cur_day_budget:.2f}")
                    b2.metric("Left", f"₹{max(remaining_today, 0):.2f}")
                    
                    if rate_per_sec > 0 and remaining_today > 0:
                        mins_left = (remaining_today / rate_per_sec) / 60.0
                        b3.metric("Time Left", f"{mins_left:.1f} min")
                    elif remaining_today <= 0:
                        b3.metric("Time Left", "DONE")
                    else:
                        b3.metric("Time Left", "∞")

                with st.container(border=True):
                    st.markdown("#### 📅 5. Monthly Ensemble Forecast")
                    daily_df = build_daily_summary_df()
                    forecast = forecast_month_ensemble(daily_df, monthly_budget)
                    if forecast is None:
                        st.info("Waiting for first completed day to generate month forecast...")
                    else:
                        m1, m2 = st.columns(2)
                        m1.metric("Projected Month", f"₹{forecast['ensemble_total']:.2f}")
                        m2.metric("Overrun Risk", f"{forecast['overrun_prob']*100:.1f}%")
                        st.caption(f"Based on {forecast['days_done']} observed days. EWMA: ₹{forecast['ewma_month_total']:.2f} | Trend: ₹{forecast['trend_month_total']:.2f}")

                with st.container(border=True):
                    st.markdown("#### 💡 6. What-If Simulator")
                    s1, s2 = st.columns(2)
                    wi_power = s1.slider("Simulate Power (W)", 0.1, 2500.0, float(round(cur_watt, 1)), 0.1, key="ml_wip")
                    wi_hours = s2.slider("Simulate Hours/Day", 1, 24, 8, key="ml_wih")
                    pred_daily = (wi_power / 1000.0) * wi_hours * 8.0
                    pred_month = pred_daily * 30.0
                    
                    w1, w2 = st.columns(2)
                    w1.metric("Sim Daily Cost", f"₹{pred_daily:.2f}")
                    w2.metric("Sim Monthly Cost", f"₹{pred_month:.2f}")

# ─────────────────────────────────────────────
time.sleep(1)
st.rerun()