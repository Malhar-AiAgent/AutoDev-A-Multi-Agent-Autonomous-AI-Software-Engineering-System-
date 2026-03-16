# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')

import subprocess
import time
import json
import re
import shutil, os, stat
import urllib.request
from glob import glob
from dotenv import load_dotenv

try:
    import psutil
except Exception:
    psutil = None

# ==========================
# 🔧 Environment Setup
# ==========================
load_dotenv()
MAX_AUTOFIX_ATTEMPTS = int(os.getenv("MAX_AUTOFIX_ATTEMPTS", "2"))
MAX_REGEN_CYCLES = int(os.getenv("MAX_REGEN_CYCLES", "5"))
MAX_FEEDBACK_CYCLES = int(os.getenv("MAX_FEEDBACK_CYCLES", "3"))
EXIT_ON_EMPTY_FEEDBACK = os.getenv("EXIT_ON_EMPTY_FEEDBACK", "1") == "1"
CONTRACT_PATH = os.path.join("core", "student_api_contract.json")
AUTOFIX_SCRIPT = "autofix_agent.py"
FULLSTACK_SCRIPT = "fullstack_agent.py"

# ==========================
# Utility Functions
# ==========================
def run_step(step_name, command, exit_on_error=True):
    print(f"\n🧩 {step_name}...")
    try:
        subprocess.run([sys.executable, command], check=True)
        print(f"✅ {step_name} completed successfully!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {step_name} failed with error code {e.returncode}.")
        if exit_on_error:
            sys.exit(1)
        raise
    except Exception as e:
        print(f"❌ Unexpected error during {step_name}: {e}")
        if exit_on_error:
            sys.exit(1)
        raise


def validate_env_vars():
    required_vars = ["GROQ_API_KEY"]
    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        print("➡️ Please update your .env file and re-run the pipeline.")
        sys.exit(1)


def resolve_pm_agent_script():
    candidates = ["pm_agent.py", "PM_Agent.py"]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    print("❌ PM Agent script not found. Expected pm_agent.py or PM_Agent.py.")
    sys.exit(1)


def remove_readonly(func, path, _):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def clear_file_if_exists(path):
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception as e:
            print(f"⚠️ Could not remove {path}: {e}")


def read_text_file(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def is_identifier_present(text, token):
    if not text or not token:
        return False
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])"
    return re.search(pattern, text) is not None


def load_active_api_contract():
    if not os.path.exists(CONTRACT_PATH):
        return None

    try:
        with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
            contract = json.load(f)
    except Exception as e:
        print(f"⚠️ Could not read API contract at {CONTRACT_PATH}: {e}")
        return None

    pm_files = sorted(glob(os.path.join("output", "pm_agent_chat_*.json")), reverse=True)
    project_text = ""
    if pm_files:
        try:
            with open(pm_files[0], "r", encoding="utf-8") as f:
                pm_data = json.load(f)
            project_text = (pm_data.get("project_idea", "") or "").lower()
        except Exception:
            project_text = ""

    activation_keywords = contract.get("activation", {}).get("any_keywords", [])
    if activation_keywords and not any(k.lower() in project_text for k in activation_keywords):
        return None

    return contract


def validate_generated_contract():
    contract = load_active_api_contract()
    if not contract:
        print("ℹ️ Contract guard skipped (no active contract for this project).")
        return True

    backend_path = os.path.join("output", "backend", "app.py")
    preview_dir = os.path.join("output", "frontend", "preview")
    backend_text = read_text_file(backend_path)

    frontend_chunks = []
    if os.path.isdir(preview_dir):
        for name in sorted(os.listdir(preview_dir)):
            if name.endswith((".js", ".html")):
                frontend_chunks.append(read_text_file(os.path.join(preview_dir, name)))
    frontend_text = "\n".join(frontend_chunks)

    errors = []

    if not backend_text:
        errors.append(f"Missing or unreadable backend app at {backend_path}")
    if not frontend_chunks:
        errors.append(f"Missing frontend preview assets at {preview_dir}")

    for token in contract.get("forbidden_fields", []):
        if is_identifier_present(backend_text, token):
            errors.append(f"Forbidden identifier '{token}' found in backend output")
        if is_identifier_present(frontend_text, token):
            errors.append(f"Forbidden identifier '{token}' found in frontend output")

    for field in contract.get("required_fields", []):
        if not is_identifier_present(backend_text, field):
            errors.append(f"Required backend field '{field}' not detected")

    for key in contract.get("post_put_required_keys", []):
        if not is_identifier_present(frontend_text, key):
            errors.append(f"Required frontend payload key '{key}' not detected")

    for route_fragment in contract.get("required_backend_route_fragments", []):
        if route_fragment not in backend_text:
            errors.append(f"Required backend route fragment '{route_fragment}' missing")

    for route_fragment in contract.get("required_frontend_path_fragments", []):
        if route_fragment not in frontend_text:
            errors.append(f"Required frontend route fragment '{route_fragment}' missing")

    api_base = contract.get("api_base")
    if api_base and api_base not in frontend_text:
        errors.append(f"Required frontend API base '{api_base}' missing")

    if errors:
        print("\n🧷 Contract guard failed:")
        for err in sorted(set(errors)):
            print(f"   - {err}")
        return False

    print("✅ Contract guard passed.")
    return True


def terminate_preview_processes(ports=None):
    ports = ports or {5000, 8000}
    if psutil is None:
        print("ℹ️ psutil not available; skipping stale process cleanup.")
        return

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "python" not in name:
                continue
            for conn in proc.net_connections(kind="inet"):
                local_port = getattr(conn.laddr, "port", None) if conn.laddr else None
                if local_port in ports:
                    print(f"🧹 Terminating stale process pid={proc.pid} on port {local_port}")
                    proc.terminate()
                    break
        except Exception:
            continue


def clear_backend_databases():
    db_dirs = [os.path.join("backend"), os.path.join("output", "backend")]
    db_exts = (".db", ".db-journal", ".db-wal", ".db-shm")

    for backend_dir in db_dirs:
        if not os.path.exists(backend_dir):
            continue

        for item in os.listdir(backend_dir):
            if item.endswith(db_exts):
                path = os.path.join(backend_dir, item)
                try:
                    os.remove(path)
                    print(f"🗑️ Removed stale DB artifact: {path}")
                except Exception as e:
                    print(f"⚠️ Could not remove DB artifact {path}: {e}")


def clear_previous_outputs():
    output_dir = "output"
    safe_keep = ["logs"]

    print("🧹 Checking for previous outputs...\n")

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print("📁 Created missing output directory.\n")
        return

    for item in os.listdir(output_dir):
        item_path = os.path.join(output_dir, item)
        if item in safe_keep:
            print(f"🧾 Keeping safe folder: {item}")
            continue
        try:
            if os.path.isdir(item_path):
                print(f"🗑️ Clearing old folder: {item}")
                shutil.rmtree(item_path, onerror=remove_readonly)
            else:
                print(f"🗑️ Removing file: {item}")
                os.remove(item_path)
        except Exception as e:
            print(f"⚠️ Could not clear {item}: {e}")

    clear_backend_databases()
    print("\n✨ Output cleanup complete. Moving to next step...\n")

def clear_cycle_generation_outputs():
    """
    Clear generated backend/frontend artifacts before each FullStack generation cycle
    so stale files never leak into the next cycle.
    """
    targets = [
        os.path.join("output", "backend"),
        os.path.join("output", "frontend", "preview"),
    ]
    files = [
        os.path.join("output", "fullstack_raw_output.txt"),
        os.path.join("logs", "frontend_runtime_errors.jsonl"),
        "error_feedback.txt",
    ]

    for target in targets:
        if os.path.isdir(target):
            try:
                print(f"🧹 Clearing cycle folder: {target}")
                shutil.rmtree(target, onerror=remove_readonly)
            except Exception as e:
                print(f"⚠️ Could not clear cycle folder {target}: {e}")

    for file_path in files:
        clear_file_if_exists(file_path)


def run_codegen_cycle(pm_agent_script, label_suffix=""):
    suffix = f" ({label_suffix})" if label_suffix else ""
    run_step(f"🧠 PM Agent{suffix}", pm_agent_script)
    clear_cycle_generation_outputs()
    run_step(f"🧩 FullStack Agent{suffix}", FULLSTACK_SCRIPT)


def wait_for_http(url, timeout_sec=20):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                code = response.getcode()
                if code and code < 500:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def start_review_preview():
    backend_path = os.path.abspath(os.path.join("output", "backend", "app.py"))
    frontend_dir = os.path.abspath(os.path.join("output", "frontend", "preview"))

    if not os.path.exists(backend_path):
        print(f"⚠️ Missing backend for review: {backend_path}")
        return None, None, None
    if not os.path.isdir(frontend_dir):
        print(f"⚠️ Missing frontend preview directory for review: {frontend_dir}")
        return None, None, None

    os.makedirs("logs", exist_ok=True)
    backend_log_path = os.path.join("logs", "review_backend_log.txt")
    backend_log_stream = open(backend_log_path, "a", encoding="utf-8")

    backend_proc = subprocess.Popen(
        [sys.executable, backend_path],
        cwd=os.path.dirname(backend_path),
        stdout=backend_log_stream,
        stderr=subprocess.STDOUT,
        text=True,
    )
    frontend_proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", "8000"],
        cwd=frontend_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    backend_ok = wait_for_http("http://127.0.0.1:5000/", timeout_sec=20)
    frontend_ok = wait_for_http("http://localhost:8000/index.html", timeout_sec=20)
    if not (backend_ok and frontend_ok):
        print("⚠️ Live review preview failed to start cleanly.")
        stop_review_preview(frontend_proc, backend_proc, backend_log_stream)
        return None, None, None

    return frontend_proc, backend_proc, backend_log_stream


def stop_review_preview(frontend_proc, backend_proc, backend_log_stream):
    for proc, label in [(frontend_proc, "frontend"), (backend_proc, "backend")]:
        if not proc:
            continue
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
            print(f"🛑 Stopped review {label} server.")
        except Exception:
            pass

    if backend_log_stream:
        try:
            backend_log_stream.flush()
            backend_log_stream.close()
        except Exception:
            pass


# ==========================
# 🚀 Main Execution
# ==========================
if __name__ == "__main__":
    print("🚀 Auto_Dev Pipeline Starting...\n")

    validate_env_vars()
    pm_agent_script = resolve_pm_agent_script()
    terminate_preview_processes({5000, 8000})
    clear_previous_outputs()

    # --- Core build pipeline ---
    run_codegen_cycle(pm_agent_script, label_suffix="Initial")

    print("\n⏳ Preparing to launch preview...")
    time.sleep(2)

    regen_cycle = 0
    feedback_cycles = 0

    while True:
        try:
            terminate_preview_processes({5000, 8000})
            clear_file_if_exists("error_feedback.txt")

            if not validate_generated_contract():
                raise subprocess.CalledProcessError(returncode=1, cmd="contract_guard")

            run_step("🧪 Step 3: Running Tester Agent for Preview", "tester_agent.py", exit_on_error=False)

        except subprocess.CalledProcessError:
            autofix_resolved = False
            if MAX_AUTOFIX_ATTEMPTS > 0 and os.path.exists(AUTOFIX_SCRIPT):
                for attempt in range(1, MAX_AUTOFIX_ATTEMPTS + 1):
                    print(f"\n🛠️ AutoFix attempt {attempt}/{MAX_AUTOFIX_ATTEMPTS}...")
                    try:
                        run_step("🛠️ Running AutoFix Agent", AUTOFIX_SCRIPT, exit_on_error=False)
                    except subprocess.CalledProcessError as autofix_err:
                        if autofix_err.returncode == 2:
                            print("ℹ️ AutoFix reported no changes.")
                        else:
                            print(f"⚠️ AutoFix failed with code {autofix_err.returncode}.")
                        break

                    terminate_preview_processes({5000, 8000})
                    clear_file_if_exists("error_feedback.txt")
                    try:
                        if not validate_generated_contract():
                            raise subprocess.CalledProcessError(returncode=1, cmd="contract_guard")
                        run_step("🧪 Tester Agent (Post-AutoFix)", "tester_agent.py", exit_on_error=False)
                        print("✅ AutoFix resolved tester failures.")
                        autofix_resolved = True
                        regen_cycle = 0
                        break
                    except subprocess.CalledProcessError:
                        print("⚠️ Tester still failing after AutoFix attempt.")

            if autofix_resolved:
                pass
            else:
                regen_cycle += 1
                if regen_cycle > MAX_REGEN_CYCLES:
                    print(f"\n❌ Max regeneration cycles reached ({MAX_REGEN_CYCLES}). Exiting.")
                    sys.exit(1)

                print(f"\n⚙️ Regeneration cycle {regen_cycle}/{MAX_REGEN_CYCLES}...\n")

                run_codegen_cycle(pm_agent_script, label_suffix=f"Auto-Retry {regen_cycle}")
                time.sleep(2)
                continue

        if regen_cycle:
            regen_cycle = 0

        terminate_preview_processes({5000, 8000})
        review_frontend_proc, review_backend_proc, review_backend_log = start_review_preview()
        if not (review_frontend_proc and review_backend_proc):
            regen_cycle += 1
            if regen_cycle > MAX_REGEN_CYCLES:
                print(f"\n❌ Max regeneration cycles reached ({MAX_REGEN_CYCLES}) after preview launch failure. Exiting.")
                sys.exit(1)
            print(f"\n⚙️ Regeneration cycle {regen_cycle}/{MAX_REGEN_CYCLES} (preview launch failure)...\n")
            run_codegen_cycle(pm_agent_script, label_suffix=f"Preview-Retry {regen_cycle}")
            time.sleep(2)
            continue

        try:
            print("\n👀 Review your app at: http://localhost:8000")
            feedback = input("💬 PM Agent: Do you want to make any changes? (type feedback or 'done'): ").strip().lower()
        finally:
            stop_review_preview(review_frontend_proc, review_backend_proc, review_backend_log)
            terminate_preview_processes({5000, 8000})

        if feedback == "done":
            print("\n✅ Project finalized successfully! Exiting pipeline...")
            break
        elif feedback:
            feedback_cycles += 1
            if feedback_cycles > MAX_FEEDBACK_CYCLES:
                print("\n🛑 Max feedback cycles reached. Exiting.")
                break

            with open("feedback.txt", "w", encoding="utf-8") as f:
                f.write(feedback)

            run_codegen_cycle(pm_agent_script, label_suffix=f"Feedback {feedback_cycles}")
            time.sleep(2)
        else:
            if EXIT_ON_EMPTY_FEEDBACK:
                print("\n✅ No feedback detected. Finalizing and exiting pipeline.")
                break
            print("\nℹ️ Empty feedback ignored. Type feedback text or 'done'.")
            continue
