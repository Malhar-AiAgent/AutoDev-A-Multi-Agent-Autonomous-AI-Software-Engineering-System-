# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import subprocess
import time
import webbrowser
import re
import json
import urllib.request
import urllib.error
import urllib.parse
from glob import glob
from datetime import datetime

try:
    import psutil
except Exception:
    psutil = None


# ==============================
# ⚙️ Configuration
# ==============================
FRONTEND_DIR = os.path.abspath("output/frontend/preview")
BACKEND_PATH = os.path.abspath("output/backend/app.py")
LOG_DIR = os.path.abspath("logs")
FEEDBACK_FILE = os.path.abspath("error_feedback.txt")
API_BASE = "http://127.0.0.1:5000"
FRONTEND_ORIGIN = "http://localhost:8000"
FRONTEND_RUNTIME_ERRORS = os.path.abspath(os.path.join("logs", "frontend_runtime_errors.jsonl"))
TEST_STATUS = "completed"
TESTER_MAX_MONITOR_SECONDS = int(os.getenv("TESTER_MAX_MONITOR_SECONDS", "90"))
STRICT_FRONTEND_CONSOLE_ERRORS = os.getenv("STRICT_FRONTEND_CONSOLE_ERRORS", "1") == "1"
CONTRACT_PATH = os.path.abspath(os.path.join("core", "student_api_contract.json"))

os.makedirs(LOG_DIR, exist_ok=True)

# ✅ STRICT pip allowlist (PERMANENT FIX)
ALLOWED_PIP_PACKAGES = {
    "flask",
    "flask_cors",
    "requests",
    "numpy",
    "pandas",
}
PIP_PACKAGE_MAP = {
    "flask_cors": "flask-cors",
}

# Stdlib modules (never install)
STDLIB_MODULES = {
    "os","sys","time","json","re","subprocess","sqlite3","math",
    "datetime","pathlib","typing","logging","threading","itertools"
}

AUTO_FIX_MAP = {
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
}

# ==============================
# 🧩 Utilities
# ==============================
def log(msg):
    print(msg)
    with open(os.path.join(LOG_DIR, "tester_log.txt"), "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")

def handle_error(message):
    global TEST_STATUS
    TEST_STATUS = "failed"
    log(f"❌ ERROR: {message}")
    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(message + "\n")

def terminate_processes_on_ports(ports):
    """
    Kill stale python preview/backend processes that keep ports occupied
    between pipeline runs.
    """
    if psutil is None:
        log("ℹ️ psutil not available; skipping stale process cleanup.")
        return

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "python" not in name:
                continue
            for conn in proc.net_connections(kind="inet"):
                local_port = getattr(conn.laddr, "port", None) if conn.laddr else None
                if local_port in ports:
                    log(f"🧹 Terminating stale process pid={proc.pid} on port {local_port}")
                    proc.terminate()
                    break
        except Exception:
            continue

def shutdown_process(proc, name):
    if not proc:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        log(f"🛑 Stopped {name}.")
    except Exception:
        pass

def clear_frontend_runtime_error_log():
    if os.path.exists(FRONTEND_RUNTIME_ERRORS):
        try:
            os.remove(FRONTEND_RUNTIME_ERRORS)
            return
        except Exception:
            try:
                with open(FRONTEND_RUNTIME_ERRORS, "w", encoding="utf-8") as f:
                    f.write("")
            except Exception:
                pass

def read_frontend_runtime_errors():
    if not os.path.exists(FRONTEND_RUNTIME_ERRORS):
        return []

    entries = []
    try:
        with open(FRONTEND_RUNTIME_ERRORS, "r", encoding="utf-8", errors="ignore") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        entries.append(parsed)
                    else:
                        entries.append({"kind": "unknown", "payload": {"raw": str(parsed)}})
                except Exception:
                    entries.append({"kind": "unknown", "payload": {"raw": line}})
    except Exception:
        return []

    return entries

def summarize_frontend_runtime_error(entry):
    kind = str(entry.get("kind", "unknown"))
    href = str(entry.get("href", ""))
    payload = entry.get("payload", {})
    if not isinstance(payload, dict):
        payload = {"raw": str(payload)}

    message = payload.get("message")
    if not message:
        reason = payload.get("reason")
        if isinstance(reason, dict):
            message = reason.get("message") or reason.get("stack")
        elif reason:
            message = str(reason)

    if not message and isinstance(payload.get("args"), list) and payload.get("args"):
        parts = []
        for arg in payload["args"][:3]:
            if isinstance(arg, dict):
                arg_message = arg.get("message") or arg.get("name") or json.dumps(arg, ensure_ascii=True)
                parts.append(str(arg_message))
            else:
                parts.append(str(arg))
        message = " | ".join(parts)

    message = str(message or "No message")
    if len(message) > 200:
        message = message[:200] + "..."

    location = f" on {href}" if href else ""
    return f"{kind}{location}: {message}"

def is_actionable_frontend_runtime_error(entry):
    kind = str(entry.get("kind", "")).lower()
    if kind in {"window.error", "window.unhandledrejection"}:
        return True

    if kind == "console.error":
        if STRICT_FRONTEND_CONSOLE_ERRORS:
            return True

        payload = entry.get("payload", {})
        args = payload.get("args", []) if isinstance(payload, dict) else []
        arg_text = " ".join(
            json.dumps(arg, ensure_ascii=True).lower() if not isinstance(arg, str) else arg.lower()
            for arg in args
        )
        summary = (summarize_frontend_runtime_error(entry) + " " + arg_text).lower()
        keywords = [
            "uncaught",
            "typeerror",
            "referenceerror",
            "syntaxerror",
            "failed to construct",
            "cannot read",
            "is not a function",
            "failed to fetch",
            "formdata",
        ]
        return any(token in summary for token in keywords)

    return False

def http_request(method, url, json_body=None, headers=None, timeout=8):
    req_headers = dict(headers or {})
    payload = None
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(url, data=payload, headers=req_headers, method=method)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="ignore")
            return response.getcode(), dict(response.headers.items()), body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        return e.code, dict(e.headers.items()) if e.headers else {}, body
    except Exception as e:
        return None, {}, str(e)

def wait_for_http(url, label, timeout_sec=45):
    start = time.time()
    while time.time() - start < timeout_sec:
        status, _, _ = http_request("GET", url, timeout=3)
        if status and status < 500:
            return True
        time.sleep(1)
    handle_error(f"{label} is not reachable: {url}")
    return False

# ==============================
# 📦 Dependency Handling (SAFE)
# ==============================
def install_dependencies_from_code(code_text):
    """
    Install ONLY known pip packages.
    Never install stdlib modules or local project modules.
    """

    # 🔒 Hard allowlist (ONLY these can be pip-installed)
    detected = set()

    for line in code_text.splitlines():
        m = re.match(r"^\s*(?:import|from)\s+([a-zA-Z0-9_]+)", line)
        if m:
            detected.add(m.group(1).lower())

    # 🔒 Filter strictly
    pip_deps = sorted({
        PIP_PACKAGE_MAP.get(pkg, pkg)
        for pkg in detected
        if pkg in ALLOWED_PIP_PACKAGES
    })

    if not pip_deps:
        log("✅ No pip dependencies required.")
        return

    log(f"📦 Installing pip dependencies: {', '.join(pip_deps)}")

    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", *pip_deps],
            check=True
        )
    except Exception as e:
        handle_error(f"Dependency installation failed: {e}")


# ==============================
# 🔍 Validation (with wait)
# ==============================
def wait_for_path(path, label, timeout_sec=60):
    start = time.time()
    while time.time() - start < timeout_sec:
        if os.path.exists(path):
            return True
        time.sleep(1)
    handle_error(f"{label} missing after {timeout_sec}s")
    return False

if not wait_for_path(FRONTEND_DIR, "Frontend directory"):
    sys.exit(1)

if not wait_for_path(BACKEND_PATH, "Backend app.py"):
    sys.exit(1)

INDEX_HTML = os.path.join(FRONTEND_DIR, "index.html")
if not wait_for_path(INDEX_HTML, "index.html"):
    sys.exit(1)

# ==============================
# ✅ Frontend/Backend Integration Validation
# ==============================
BACKEND_CODE_PATH = BACKEND_PATH

def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

def is_identifier_present(text: str, token: str) -> bool:
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
        log(f"⚠️ Could not read API contract at {CONTRACT_PATH}: {e}")
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

    log(f"📘 Tester enforcing API contract: {contract.get('name', 'unnamed_contract')}")
    return contract

def validate_active_contract(backend_text: str, frontend_text: str) -> bool:
    contract = load_active_api_contract()
    if not contract:
        log("ℹ️ Contract guard skipped (no active contract for this project).")
        return True

    errors = []

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
        for err in sorted(set(errors)):
            handle_error(f"Contract guard failed: {err}")
        return False

    log("✅ Contract guard passed.")
    return True

def collect_frontend_js(directory: str) -> str:
    chunks = []

    for fname in os.listdir(directory):
        path = os.path.join(directory, fname)
        if os.path.isfile(path) and fname.lower().endswith(".js"):
            chunks.append(read_text(path))

    inline_pattern = r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>"
    for fname in os.listdir(directory):
        path = os.path.join(directory, fname)
        if os.path.isfile(path) and fname.lower().endswith(".html"):
            html = read_text(path)
            inline_scripts = re.findall(inline_pattern, html, flags=re.IGNORECASE | re.DOTALL)
            chunks.extend(inline_scripts)

    return "\n".join(chunks)

def has_direct_local_api_fetch(js_text: str) -> bool:
    pattern = r"fetch\(\s*[`'\"]https?://(?:localhost|127\.0\.0\.1):5000(?:/|`|'|\"|\?)"
    return re.search(pattern, js_text) is not None

def extract_absolute_fetch_urls(js_text: str):
    urls = []
    patterns = [
        r"fetch\(\s*`(https?://[^`]+)`",
        r"fetch\(\s*'(https?://[^']+)'",
        r'fetch\(\s*"(https?://[^"]+)"',
    ]
    for pattern in patterns:
        urls.extend(re.findall(pattern, js_text))
    return urls

frontend_js = collect_frontend_js(FRONTEND_DIR)
backend_py = read_text(BACKEND_CODE_PATH)

if backend_py:
    install_dependencies_from_code(backend_py)

if not validate_active_contract(backend_py, frontend_js):
    sys.exit(1)

# Require API usage when backend exists
if backend_py:
    if "fetch(" not in frontend_js:
        handle_error("Frontend does not call backend API (missing fetch in frontend JS)")
        sys.exit(1)
    if "API_BASE" not in frontend_js and not has_direct_local_api_fetch(frontend_js):
        handle_error(
            "Frontend missing API_BASE constant or direct fetch URL to local backend :5000"
        )
        sys.exit(1)

# Basic route alignment check (best-effort)
def extract_route_methods(py_text: str):
    """
    Parse @app.route decorators and capture path -> allowed HTTP methods.
    Defaults to GET when methods are not explicitly provided.
    """
    route_methods = {}
    pattern = r"@app\.route\(\s*['\"]([^'\"]+)['\"](?:\s*,\s*methods=\[([^\]]+)\])?"
    for path, methods_part in re.findall(pattern, py_text):
        methods = {"GET"}
        if methods_part:
            found = re.findall(r"['\"]([A-Za-z]+)['\"]", methods_part)
            if found:
                methods = {m.upper() for m in found}
        route_methods.setdefault(path, set()).update(methods)
    return route_methods

def extract_routes(py_text):
    return set(extract_route_methods(py_text).keys())

def normalize_fetch_path(raw_path: str) -> str:
    path = (raw_path or "").strip()
    if not path:
        return "/"

    if path.startswith("http://") or path.startswith("https://"):
        parsed = urllib.parse.urlparse(path)
        path = parsed.path or "/"

    path = path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    return path

def extract_fetch_paths(js_text):
    paths = set()
    patterns = [
        r"fetch\(\s*`\$\{API_BASE\}([^`]+)`",
        r"fetch\(\s*'\$\{API_BASE\}([^']+)'",
        r'fetch\(\s*"\$\{API_BASE\}([^"]+)"',
        r"fetch\(\s*API_BASE\s*\+\s*'([^']+)'",
        r'fetch\(\s*API_BASE\s*\+\s*"([^"]+)"',
        r"fetch\(\s*`https?://(?:localhost|127\.0\.0\.1):5000([^`]*)`",
        r"fetch\(\s*'https?://(?:localhost|127\.0\.0\.1):5000([^']*)'",
        r'fetch\(\s*"https?://(?:localhost|127\.0\.0\.1):5000([^"]*)"',
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, js_text):
            paths.add(normalize_fetch_path(match.group(1)))

    return paths

def extract_api_base_path(js_text: str) -> str:
    match = re.search(r"API_BASE\s*=\s*['\"]([^'\"]+)['\"]", js_text)
    if not match:
        return ""

    value = match.group(1).strip()
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urllib.parse.urlparse(value)
        return (parsed.path or "").rstrip("/")

    return value.rstrip("/")

def extract_api_base_value(js_text: str) -> str:
    match = re.search(r"API_BASE\s*=\s*['\"]([^'\"]+)['\"]", js_text)
    return match.group(1).strip() if match else ""

route_methods = extract_route_methods(backend_py)
backend_routes = set(route_methods.keys())
frontend_paths = extract_fetch_paths(frontend_js)
api_base_path = extract_api_base_path(frontend_js)
api_base_value = extract_api_base_value(frontend_js)
absolute_fetch_urls = extract_absolute_fetch_urls(frontend_js)

if api_base_value:
    parsed = urllib.parse.urlparse(api_base_value)
    if parsed.scheme in ("http", "https"):
        host = (parsed.hostname or "").lower()
        port = parsed.port
        if host not in {"localhost", "127.0.0.1"} or port != 5000:
            handle_error(
                f"Frontend API_BASE must target local backend on :5000, found: {api_base_value}"
            )
            sys.exit(1)
    elif api_base_value.startswith("/"):
        handle_error(
            f"Frontend API_BASE must be absolute to backend :5000, found relative path: {api_base_value}"
        )
        sys.exit(1)

for fetch_url in absolute_fetch_urls:
    parsed = urllib.parse.urlparse(fetch_url)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if parsed.scheme in ("http", "https"):
        if host not in {"localhost", "127.0.0.1"} or port != 5000:
            handle_error(
                f"Frontend fetch URL must target local backend on :5000, found: {fetch_url}"
            )
            sys.exit(1)

if backend_routes and frontend_paths:
    def path_matches_backend(fp, broutes, base_path):
        candidates = {fp}
        if base_path:
            if fp.startswith("/"):
                candidates.add(f"{base_path}{fp}")
            else:
                candidates.add(f"{base_path}/{fp}")
        if not fp.startswith("/"):
            candidates.update({f"/{c}" for c in list(candidates)})

        for candidate in candidates:
            if candidate in broutes:
                return True
        # Allow template placeholders like ${id} to match /<int:id>
            if "${" in candidate:
                prefix = candidate.split("${", 1)[0].rstrip("/")
                for br in broutes:
                    if br.startswith(prefix) and "<" in br and ">" in br:
                        return True
        return False

    missing = [p for p in frontend_paths if not path_matches_backend(p, backend_routes, api_base_path)]
    if missing:
        handle_error(
            "Frontend calls missing backend routes: "
            f"{missing}. Backend routes: {sorted(backend_routes)}. "
            f"Frontend paths: {sorted(frontend_paths)}. API_BASE path: {api_base_path or '/'}"
        )
        sys.exit(1)

# ===== DOM id consistency check =====
def collect_html_ids(directory: str):
    ids = set()
    for fname in os.listdir(directory):
        if not fname.endswith(".html"):
            continue
        path = os.path.join(directory, fname)
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            for m in re.finditer(r'id=["\']([^"\']+)["\']', text):
                ids.add(m.group(1))
        except Exception:
            continue
    return ids

def collect_js_ids(js_text: str):
    ids = set()
    for m in re.finditer(r'getElementById\(["\']([^"\']+)["\']\)', js_text):
        ids.add(m.group(1))
    for m in re.finditer(r'querySelector\(["\']#([^"\']+)["\']\)', js_text):
        ids.add(m.group(1))
    return ids

html_ids = collect_html_ids(FRONTEND_DIR)
js_ids = collect_js_ids(frontend_js)
missing_ids = sorted([i for i in js_ids if i not in html_ids])
if missing_ids:
    handle_error(f"Frontend JS references missing HTML IDs: {missing_ids}")
    sys.exit(1)

unsafe_listener_pattern = r"document\.getElementById\(['\"][^'\"]+['\"]\)\.addEventListener\("
if re.search(unsafe_listener_pattern, frontend_js):
    handle_error("Frontend JS uses unsafe direct addEventListener on getElementById result")
    sys.exit(1)

# ==============================
# 🚀 Run Servers
# ==============================
def run_api_smoke_tests(route_methods_map):
    static_routes = sorted([p for p in route_methods_map.keys() if "<" not in p])
    if not static_routes:
        handle_error("No static backend routes found for smoke testing")
        return False

    preferred_methods = ["POST", "PUT", "PATCH", "DELETE", "GET"]
    cors_target = static_routes[0]
    cors_method = "GET"
    for method in preferred_methods:
        candidates = sorted([
            path for path, methods in route_methods_map.items()
            if "<" not in path and method in methods
        ])
        if candidates:
            cors_target = candidates[0]
            cors_method = method
            break

    cors_status, cors_headers, cors_body = http_request(
        "OPTIONS",
        f"{API_BASE}{cors_target}",
        headers={
            "Origin": FRONTEND_ORIGIN,
            "Access-Control-Request-Method": cors_method,
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    if cors_status is None or cors_status >= 500:
        handle_error(f"CORS preflight failed on {cors_target}: {cors_status} {cors_body[:200]}")
        return False

    acao = cors_headers.get("Access-Control-Allow-Origin") or cors_headers.get("access-control-allow-origin")
    if not acao:
        handle_error(f"CORS header missing on {cors_target} preflight response")
        return False
    if acao not in {"*", FRONTEND_ORIGIN}:
        handle_error(
            f"CORS header mismatch on {cors_target}: expected {FRONTEND_ORIGIN} or '*', got {acao}"
        )
        return False

    get_routes = [p for p, methods in route_methods_map.items() if "GET" in methods and "<" not in p]
    post_routes = [p for p, methods in route_methods_map.items() if "POST" in methods and "<" not in p]

    for path in get_routes[:5]:
        status, _, body = http_request("GET", f"{API_BASE}{path}")
        if status is None or status >= 500:
            handle_error(f"GET smoke test failed on {path}: {status} {body[:200]}")
            return False

    for path in post_routes[:5]:
        status, _, body = http_request(
            "POST",
            f"{API_BASE}{path}",
            json_body={},
            headers={"Origin": FRONTEND_ORIGIN},
        )
        if status is None or status >= 500:
            handle_error(f"POST smoke test failed on {path}: {status} {body[:200]}")
            return False

    log("✅ API smoke tests passed (OPTIONS/GET/POST).")
    return True

backend_proc = None
frontend_proc = None
backend_log_stream = None

try:
    clear_frontend_runtime_error_log()
    terminate_processes_on_ports({5000, 8000})

    log("🚀 Starting backend server...")
    backend_log_stream = open(os.path.join(LOG_DIR, "error_log.txt"), "a", encoding="utf-8")
    backend_proc = subprocess.Popen(
        ["python", BACKEND_PATH],
        cwd=os.path.dirname(BACKEND_PATH),
        stdout=backend_log_stream,
        stderr=subprocess.STDOUT,
        text=True
    )

    log("🌐 Starting frontend server...")
    frontend_proc = subprocess.Popen(
        ["python", "-m", "http.server", "8000"],
        cwd=FRONTEND_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True
    )

    backend_probe_path = next((p for p, m in route_methods.items() if "GET" in m and "<" not in p), "/")
    if not wait_for_http(f"{API_BASE}{backend_probe_path}", "Backend API"):
        sys.exit(1)
    if not wait_for_http("http://localhost:8000/index.html", "Frontend server"):
        sys.exit(1)
    if not run_api_smoke_tests(route_methods):
        sys.exit(1)

    webbrowser.open("http://localhost:8000")
    log("✅ Preview running at http://localhost:8000")

    last_backend_health_check = 0.0
    last_frontend_error_check = 0.0
    seen_frontend_error_count = len(read_frontend_runtime_errors())
    monitor_start_time = time.time()
    while True:
        if frontend_proc.poll() is not None:
            handle_error(f"Frontend server exited unexpectedly with code {frontend_proc.returncode}")
            sys.exit(1)

        now = time.time()
        if now - last_backend_health_check >= 2.0:
            last_backend_health_check = now
            status, _, body = http_request("GET", f"{API_BASE}{backend_probe_path}", timeout=4)
            if status is None or status >= 500:
                handle_error(f"Backend health check failed: {status} {body[:200]}")
                sys.exit(1)

        if now - last_frontend_error_check >= 2.0:
            last_frontend_error_check = now
            runtime_errors = read_frontend_runtime_errors()
            if len(runtime_errors) > seen_frontend_error_count:
                new_entries = runtime_errors[seen_frontend_error_count:]
                seen_frontend_error_count = len(runtime_errors)
                actionable = [e for e in new_entries if is_actionable_frontend_runtime_error(e)]
                if actionable:
                    for entry in actionable[:5]:
                        handle_error("Frontend runtime error captured: " + summarize_frontend_runtime_error(entry))
                    sys.exit(1)

        if TESTER_MAX_MONITOR_SECONDS > 0 and (now - monitor_start_time) >= TESTER_MAX_MONITOR_SECONDS:
            log(
                "✅ Tester monitor window complete with no critical failures "
                f"({TESTER_MAX_MONITOR_SECONDS}s)."
            )
            break

        time.sleep(0.5)

except KeyboardInterrupt:
    log("🛑 Tester stopped by user.")

finally:
    shutdown_process(frontend_proc, "frontend server")
    shutdown_process(backend_proc, "backend server")
    terminate_processes_on_ports({5000, 8000})
    if backend_log_stream:
        backend_log_stream.flush()
        backend_log_stream.close()

    summary = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "frontend": FRONTEND_DIR,
        "backend": BACKEND_PATH,
        "status": TEST_STATUS
    }
    summary_path = os.path.join(LOG_DIR, f"test_summary_{int(time.time())}.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=4)
    log(f"📄 Test summary saved → {summary_path}")
