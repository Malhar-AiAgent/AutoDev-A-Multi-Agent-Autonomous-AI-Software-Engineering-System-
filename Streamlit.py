# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")

import streamlit as st
import subprocess
import threading
import time
import psutil
import shutil
import os
import stat

# ==============================
# ⚙️ PAGE CONFIG
# ==============================
st.set_page_config(
    page_title="Auto_Dev Pipeline",
    layout="wide",
    page_icon="🧠"
)

st.title("🧠 Auto_Dev Pipeline Dashboard")
st.caption("FullStack autonomous pipeline — Streamlit edition ⚡")

# ==============================
# ---------- GLOBAL STATE ----------
# ==============================
shared_state = {
    "logs": [],
    "progress": {
        "PM Agent": 0,
        "FullStack Agent": 0,
        "AutoFix Agent": 0,
        "Tester Agent": 0,
    },
    "status": "Idle",
    "running": False
}

# ==============================
# ---------- SAFE LOGGING ----------
# ==============================
def safe_log(msg):
    timestamp = time.strftime("%H:%M:%S")
    shared_state["logs"].append(f"[{timestamp}] {msg}")
    print(msg)

# ==============================
# ---------- FILE UTILITIES ----------
# ==============================
def remove_readonly(func, path, _):
    os.chmod(path, stat.S_IWRITE)
    func(path)

def safe_delete_folder(folder_path):
    if not os.path.exists(folder_path):
        return
    try:
        shutil.rmtree(folder_path, onerror=remove_readonly)
        safe_log(f"🗑️ Removed folder: {folder_path}")
    except Exception as e:
        safe_log(f"⚠️ Could not delete {folder_path}: {e}")

def terminate_preview_processes(ports=None):
    ports = ports or {5000, 8000}
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "python" not in name:
                continue
            for conn in proc.net_connections(kind="inet"):
                local_port = getattr(conn.laddr, "port", None) if conn.laddr else None
                if local_port in ports:
                    safe_log(f"🧹 Terminating stale process pid={proc.pid} on port {local_port}")
                    proc.terminate()
                    break
        except Exception:
            continue

def clear_logs_folder():
    LOG_DIRS = ["logs", "output/logs"]
    for LOGS_DIR in LOG_DIRS:
        if not os.path.exists(LOGS_DIR):
            os.makedirs(LOGS_DIR)
            continue
        for f in os.listdir(LOGS_DIR):
            path = os.path.join(LOGS_DIR, f)
            try:
                if f.endswith((".txt", ".json")):
                    os.remove(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path, onerror=remove_readonly)
            except Exception:
                pass

def clear_db_artifacts():
    db_dirs = [os.path.join("backend"), os.path.join("output", "backend")]
    db_exts = (".db", ".db-journal", ".db-wal", ".db-shm")

    for db_dir in db_dirs:
        if not os.path.isdir(db_dir):
            continue
        for item in os.listdir(db_dir):
            if not item.endswith(db_exts):
                continue
            path = os.path.join(db_dir, item)
            try:
                os.remove(path)
                safe_log(f"🗑️ Removed DB artifact: {path}")
            except Exception as e:
                safe_log(f"⚠️ Could not remove DB artifact {path}: {e}")

def clean_generated_files():
    output_dir = "output"
    safe_log("🧹 Cleaning generated output...")
    terminate_preview_processes({5000, 8000})

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    for item in os.listdir(output_dir):
        path = os.path.join(output_dir, item)
        if item == "logs":
            clear_logs_folder()
            continue
        try:
            if os.path.isdir(path):
                safe_delete_folder(path)
            else:
                os.remove(path)
        except Exception:
            pass

    clear_db_artifacts()

def clear_feedback_files():
    for path in ["error_feedback.txt", "feedback.txt"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

def clear_cycle_generation_outputs():
    """
    Clear generated backend/frontend artifacts before each FullStack generation cycle
    so stale files never leak into the next cycle.
    """
    folders = [
        os.path.join("output", "backend"),
        os.path.join("output", "frontend", "preview"),
    ]
    files = [
        os.path.join("output", "fullstack_raw_output.txt"),
        os.path.join("logs", "frontend_runtime_errors.jsonl"),
        "error_feedback.txt",
    ]

    for folder in folders:
        if os.path.isdir(folder):
            safe_delete_folder(folder)

    for file_path in files:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

# ==============================
# ---------- COMMAND RUNNER ----------
# ==============================
def run_command(cmd, agent_name):
    safe_log(f"▶️ Starting {agent_name}...")
    shared_state["progress"][agent_name] = 10

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    for line in process.stdout:
        safe_log(line.strip())
        shared_state["progress"][agent_name] = min(
            90, shared_state["progress"][agent_name] + 2
        )
        time.sleep(0.05)

    process.wait()

    if process.returncode == 0:
        shared_state["progress"][agent_name] = 100
        safe_log(f"✅ {agent_name} completed.\n")
        return True
    else:
        shared_state["progress"][agent_name] = 0
        safe_log(f"❌ {agent_name} failed.\n")
        return False

# ==============================
# ---------- PIPELINE ----------
# ==============================
def run_pipeline():
    max_autofix_attempts = int(os.getenv("MAX_AUTOFIX_ATTEMPTS", "2"))
    max_regen_cycles = int(os.getenv("MAX_REGEN_CYCLES", "3"))

    def run_codegen_cycle():
        if not run_command([sys.executable, "pm_agent.py"], "PM Agent"):
            return False, "PM Agent"

        clear_cycle_generation_outputs()
        safe_log("🧹 Cleared stale generated artifacts for this cycle.")

        if not run_command([sys.executable, "fullstack_agent.py"], "FullStack Agent"):
            return False, "FullStack Agent"

        return True, None

    shared_state["running"] = True
    shared_state["status"] = "Running"
    safe_log("🚀 Auto_Dev pipeline initiated...")
    clear_feedback_files()
    safe_log("🧹 Cleared stale feedback files for a fresh run.")

    for k in shared_state["progress"]:
        shared_state["progress"][k] = 0

    regen_cycle = 0
    while regen_cycle <= max_regen_cycles:

        ok_codegen, failed_agent = run_codegen_cycle()
        if not ok_codegen:
            shared_state["status"] = f"Failed ❌ ({failed_agent})"
            shared_state["running"] = False
            return

        tester_ok = run_command([sys.executable, "tester_agent.py"], "Tester Agent")
        if tester_ok:
            shared_state["status"] = "Completed ✅"
            shared_state["running"] = False
            safe_log("🎯 Pipeline completed successfully.")
            return

        # AutoFix Attempts
        resolved = False
        for attempt in range(max_autofix_attempts):
            safe_log(f"🛠️ Running AutoFix Agent ({attempt+1}/{max_autofix_attempts})...")
            if not run_command([sys.executable, "autofix_agent.py"], "AutoFix Agent"):
                break

            safe_log("🔁 Retesting after AutoFix patch...")
            if run_command([sys.executable, "tester_agent.py"], "Tester Agent"):
                resolved = True
                break

        if resolved:
            shared_state["status"] = "Completed ✅"
            shared_state["running"] = False
            safe_log("🎯 Pipeline completed successfully after AutoFix.")
            return

        regen_cycle += 1
        if regen_cycle > max_regen_cycles:
            shared_state["status"] = "Failed ❌ (Tester Agent)"
            shared_state["running"] = False
            safe_log("🛑 Pipeline stopped after regeneration attempts.")
            return

# ==============================
# ---------- UI ----------
# ==============================
user_input = st.text_area(
    "💬 Describe your project idea",
    placeholder="e.g., A student management system...",
    height=100
)

col1, col2, col3 = st.columns(3)

with col1:
    start_btn = st.button("🚀 Start Pipeline", disabled=shared_state["running"])

with col2:
    clean_btn = st.button("🧹 Clean Generated Files")

with col3:
    clear_logs_btn = st.button("🧾 Clear Logs")

if clean_btn:
    clean_generated_files()
    st.success("Cleanup complete.")
    st.rerun()

if clear_logs_btn:
    clear_logs_folder()
    shared_state["logs"].clear()
    shared_state["status"] = "Idle"
    st.success("Logs cleared.")
    st.rerun()

st.divider()
st.markdown(f"### 🔄 Current Status: **{shared_state['status']}**")

with st.expander("⚙️ System Metrics"):
    st.write(f"🧮 CPU Usage: {psutil.cpu_percent(interval=0.5)}%")
    st.write(f"💾 Memory Usage: {psutil.virtual_memory().percent}%")

with st.expander("📘 About"):
    st.markdown(
        """
        **Pipeline Flow**
        1️⃣ PM Agent  
        2️⃣ FullStack Agent  
        3️⃣ Tester Agent  
        4️⃣ AutoFix Agent (if tester fails)  
        """
    )

if start_btn:
    if user_input.strip():
        with open("pm_input.txt", "w", encoding="utf-8") as f:
            f.write(user_input)
        thread = threading.Thread(target=run_pipeline)
        thread.start()
    else:
        st.warning("Please enter a project idea.")

time.sleep(1)
if shared_state["running"]:
    st.rerun()
