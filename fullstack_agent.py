# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')

import os
import json
import re
import hashlib
from glob import glob
from groq import Groq
from dotenv import load_dotenv

# ===== Load Environment =====
load_dotenv()
api_key = os.getenv("GROQ_API_KEY")

if not api_key:
    print("❌ GROQ_API_KEY missing.")
    exit()

client = Groq(api_key=api_key)
model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
CONTRACT_PATH = os.path.join("core", "student_api_contract.json")
DB_CONFIG_PATH = os.path.join("core", "db_config.py")

print("\n🧩 FullStack Agent launched...\n")

# ===== Load latest PM output =====
pm_files = sorted(glob("output/pm_agent_chat_*.json"), reverse=True)
if not pm_files:
    print("❌ No PM output found.")
    exit()

pm_file = pm_files[0]

with open(pm_file, "r", encoding="utf-8") as f:
    pm_data = json.load(f)

project_idea = pm_data.get("project_idea", "")
ai_plan = pm_data.get("ai_plan", "")

def slugify_project_name(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "project"

def write_project_db_config(project_title: str):
    slug = slugify_project_name(project_title)[:24]
    suffix = hashlib.sha1((project_title or "project").encode("utf-8")).hexdigest()[:8]
    db_filename = f"{slug}_{suffix}.db"
    db_dir = os.path.abspath(os.path.join("backend"))
    db_abs_path = os.path.join(db_dir, db_filename)

    os.makedirs("core", exist_ok=True)
    with open(DB_CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(
            "# core/db_config.py\n"
            "import os\n\n"
            "BASE_DIR = os.path.dirname(os.path.abspath(__file__))\n"
            "# Project-scoped DB path (one DB file per project idea)\n"
            "_PREFERRED_DB_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', 'backend'))\n"
            "try:\n"
            "    os.makedirs(_PREFERRED_DB_DIR, exist_ok=True)\n"
            "    DB_DIR = _PREFERRED_DB_DIR\n"
            "except Exception:\n"
            "    DB_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', 'output', 'backend'))\n"
            "    os.makedirs(DB_DIR, exist_ok=True)\n"
            "DB_PATH = os.path.join(DB_DIR, '%s')\n"
            % db_filename
        )

    return db_filename, db_abs_path

db_filename, db_abs_path = write_project_db_config(project_idea)
print(f"🗄️ Project DB configured: {db_abs_path}")

def load_active_api_contract(project_text: str, plan_text: str):
    if not os.path.exists(CONTRACT_PATH):
        return None, ""
    try:
        with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
            contract = json.load(f)
    except Exception as e:
        print(f"⚠️ Could not read API contract at {CONTRACT_PATH}: {e}")
        return None, ""

    activation_keywords = contract.get("activation", {}).get("any_keywords", [])
    activation_source = (project_text or "").lower()
    if activation_keywords and not any(k.lower() in activation_source for k in activation_keywords):
        print("ℹ️ API contract present but not activated for this project.")
        return None, ""

    print(f"📘 Enforcing API contract: {contract.get('name', 'unnamed_contract')}")
    return contract, json.dumps(contract, indent=2)

active_contract, api_contract_text = load_active_api_contract(project_idea, ai_plan)
api_contract_block = f"API Contract (NON-NEGOTIABLE if provided):\n{api_contract_text}\n\n" if api_contract_text else ""

# ===== Load error feedback if exists =====
error_feedback = ""
if os.path.exists("error_feedback.txt"):
    with open("error_feedback.txt", "r", encoding="utf-8") as f:
        error_feedback = f.read().strip()
    os.remove("error_feedback.txt")
    print(f"🔧 Loaded error feedback to fix:\n{error_feedback}\n")

# ===== Prompt =====
messages = [
    {
        "role": "system",
        "content": (
    "You are a senior full-stack engineer. Your ONLY job is to generate working full-stack code that connects perfectly.\n\n"

    "Generate BOTH backend Flask code and frontend HTML/CSS/JS that communicate seamlessly.\n\n"

    "CRITICAL BACKEND RULES (MANDATORY):\n"
    "1. Use Flask only.\n"
    "2. Always include: from flask import Flask, jsonify, request\n"
    "3. Always define: app = Flask(__name__)\n"
    "4. ALWAYS enable CORS for browser calls from localhost:8000 using Flask-native headers.\n"
    "   Add an @app.after_request handler that sets Access-Control-Allow-Origin,\n"
    "   Access-Control-Allow-Methods, and Access-Control-Allow-Headers.\n"
    "   Do NOT depend on flask-cors.\n"
    "5. Always include a root health route:\n"
    "   @app.route('/')\n"
    "   def health():\n"
    "       return jsonify({'status': 'ok'})\n"
    "6. Add request.headers logging to detect frontend calls.\n"
    "7. Always start server:\n"
    "   if __name__ == '__main__':\n"
    "       app.run(host='127.0.0.1', port=5000, debug=False)\n"
    "8. Do NOT use 0.0.0.0 or external IP.\n"
    "9. Every route returns JSON.\n\n"
    "10. For JSON input, use request.get_json(silent=True) and validate required keys before use.\n\n"
    "11. For data-oriented apps, persistence is mandatory: do NOT keep data only in memory.\n"
    "12. Use SQLite via project-scoped DB_PATH:\n"
    "    from core.db_config import DB_PATH\n"
    "13. Ensure DB directory exists before connect:\n"
    "    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)\n"
    "14. Connect using sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False).\n"
    "15. Use CREATE TABLE IF NOT EXISTS for startup initialization.\n\n"

    "CRITICAL FRONTEND RULES (MANDATORY):\n"
    "1. HTML must link EXACTLY:\n"
    "   <link rel='stylesheet' href='styles.css'>\n"
    "   <script src='script.js'></script>\n"
    "2. JavaScript MUST define: const API_BASE = 'http://127.0.0.1:5000';\n"
    "3. Every fetch() must use API_BASE and match a backend @app.route() exactly.\n"
    "4. NO relative paths, NO guessing, NO /api prefix guessing.\n"
    "5. Log fetch URLs to browser console to verify.\n"
    "6. Handle fetch errors gracefully with try-catch.\n"
    "7. Guard DOM lookups before addEventListener to avoid null runtime errors.\n\n"

    "CRITICAL CONNECTION RULES:\n"
    "1. Frontend must know exact backend routes before calling.\n"
    "2. Both must use http://127.0.0.1:5000\n"
    "3. All responses must be JSON.\n"
    "4. No undefined routes or mismatches.\n"
    "5. Backend must support CORS preflight (OPTIONS) for API endpoints.\n\n"

    "API CONTRACT CONSISTENCY (MANDATORY):\n"
    "- If an API contract block is provided, endpoint paths and JSON keys are non-negotiable.\n"
    "- Keep request and response field names exactly as specified.\n"
    "- Do NOT mix camelCase and snake_case for contract-defined fields.\n\n"

    "OUTPUT FORMAT (STRICT):\n"
    "---backend---\n"
    "```python\n<code>\n```\n"
    "---frontend-html---\n"
    "```html\n<code>\n```\n"
    "---frontend-css---\n"
    "```css\n<code>\n```\n"
    "---frontend-js---\n"
    "```javascript\n<code>\n```"
)
,
    },
    {
        "role": "user",
        "content": (
            f"Project Idea:\n{project_idea}\n\n"
            f"PM Plan:\n{ai_plan}\n\n"
            f"{api_contract_block}"
            + (f"ERRORS TO FIX:\n{error_feedback}\n" if error_feedback else "")
        ),
    },
]

print("🧠 Generating full-stack application...")

response = client.chat.completions.create(
    model=model_name,
    messages=messages,
)

reply = response.choices[0].message.content

# ===== Extract blocks =====
def extract(section):
    pattern = rf"---{section}---\n```[a-zA-Z]*\n(.*?)```"
    match = re.search(pattern, reply, re.DOTALL)
    return match.group(1).strip() if match else ""

backend_code = extract("backend")
html_code = extract("frontend-html")
css_code = extract("frontend-css")
js_code = extract("frontend-js")

def ensure_flask_imports(code: str) -> str:
    if not code:
        return code
    match = re.search(r"^from flask import ([^\n]+)$", code, flags=re.MULTILINE)
    if not match:
        return code
    existing = [part.strip() for part in match.group(1).split(",") if part.strip()]
    required = ["Flask", "jsonify", "request"]
    merged = list(dict.fromkeys(existing + required))
    return code.replace(match.group(0), "from flask import " + ", ".join(merged), 1)

def ensure_line_in_imports(code: str, line: str) -> str:
    if not code:
        return code
    if re.search(rf"^{re.escape(line)}\s*$", code, flags=re.MULTILINE):
        return code

    lines = code.splitlines()
    insert_idx = 0
    while insert_idx < len(lines):
        current = lines[insert_idx].strip()
        if current.startswith("import ") or current.startswith("from "):
            insert_idx += 1
            continue
        break
    lines.insert(insert_idx, line)
    return "\n".join(lines)

def strip_root_and_db_path_artifacts(code: str) -> str:
    if not code:
        return code

    code = re.sub(
        r"ROOT_DIR\s*=\s*os\.path\.abspath\([^\n]+\)\nif ROOT_DIR not in sys\.path:\n\s+sys\.path\.append\(ROOT_DIR\)\n?",
        "",
        code,
        flags=re.MULTILINE,
    )
    code = re.sub(r"^from core\.db_config import DB_PATH\s*$\n?", "", code, flags=re.MULTILINE)
    code = re.sub(r"^DB_PATH\s*=.*$\n?", "", code, flags=re.MULTILINE)
    return code

def apply_canonical_db_header(code: str) -> str:
    if not code:
        return code

    code = strip_root_and_db_path_artifacts(code)
    lines = code.splitlines()
    filtered = []
    for line in lines:
        if line.strip() in {"import os", "import sys", "import sqlite3"}:
            continue
        filtered.append(line)

    body = "\n".join(filtered).lstrip()
    header = (
        "import os\n"
        "import sys\n"
        "import sqlite3\n"
        "ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))\n"
        "if ROOT_DIR not in sys.path:\n"
        "    sys.path.append(ROOT_DIR)\n"
        "from core.db_config import DB_PATH\n"
    )

    return header + ("\n" + body if body else "")

def ensure_safe_db_parent_creation(code: str) -> str:
    if not code:
        return code
    safe_parent_block = "_db_parent = os.path.dirname(os.path.abspath(DB_PATH)) or '.'\nos.makedirs(_db_parent, exist_ok=True)"
    if "_db_parent = os.path.dirname(os.path.abspath(DB_PATH)) or '.'" in code:
        return code

    if "os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)" in code:
        return code.replace("os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)", safe_parent_block)

    connect_def = re.search(r"^def _autodev_connect\(\):\s*$", code, flags=re.MULTILINE)
    if connect_def:
        insert_at = connect_def.start()
        return code[:insert_at] + safe_parent_block + "\n\n" + code[insert_at:]

    app_def = re.search(r"^app\s*=\s*Flask\(__name__\)\s*$", code, flags=re.MULTILINE)
    if app_def:
        insert_at = app_def.end()
        return code[:insert_at] + "\n" + safe_parent_block + "\n" + code[insert_at:]

    return safe_parent_block + "\n\n" + code

def ensure_allowed_origins_constant(code: str) -> str:
    if not code:
        return code
    if "ALLOWED_ORIGINS = {" in code:
        return code

    app_def = re.search(r"^app\s*=\s*Flask\(__name__\)\s*$", code, flags=re.MULTILINE)
    block = "ALLOWED_ORIGINS = {'http://localhost:8000', 'http://127.0.0.1:8000'}\n"
    if app_def:
        insert_at = app_def.end()
        return code[:insert_at] + "\n" + block + code[insert_at:]
    return block + "\n" + code

def normalize_static_cors_origin_assignment(code: str) -> str:
    if not code:
        return code
    pattern = r"^(\s*)response\.headers\[['\"]Access-Control-Allow-Origin['\"]\]\s*=\s*['\"]https?://(?:localhost|127\.0\.0\.1):\d+['\"]\s*$"
    if not re.search(pattern, code, flags=re.MULTILINE):
        return code

    code = re.sub(
        pattern,
        (
            r"\1origin = request.headers.get('Origin')\n"
            r"\1if origin in ALLOWED_ORIGINS:\n"
            r"\1    response.headers['Access-Control-Allow-Origin'] = origin\n"
            r"\1    response.headers['Vary'] = 'Origin'"
        ),
        code,
        flags=re.MULTILINE,
    )
    code = ensure_allowed_origins_constant(code)
    return code

def enforce_project_db_bootstrap(code: str) -> str:
    if not code:
        return code

    code = apply_canonical_db_header(code)
    code = ensure_safe_db_parent_creation(code)

    has_bootstrap = "def _autodev_connect()" in code and "def _autodev_init_meta()" in code
    if has_bootstrap:
        return code

    bootstrap_block = (
        "\ndef _autodev_connect():\n"
        "    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)\n"
        "    try:\n"
        "        conn.execute('PRAGMA journal_mode=MEMORY')\n"
        "        conn.execute('PRAGMA synchronous=NORMAL')\n"
        "    except sqlite3.OperationalError:\n"
        "        pass\n"
        "    return conn\n\n"
        "def _autodev_init_meta():\n"
        "    conn = _autodev_connect()\n"
        "    try:\n"
        "        conn.execute(\n"
        "            'CREATE TABLE IF NOT EXISTS __autodev_meta (k TEXT PRIMARY KEY, v TEXT)'\n"
        "        )\n"
        "        conn.commit()\n"
        "    finally:\n"
        "        conn.close()\n\n"
        "_autodev_init_meta()\n"
    )

    app_def = re.search(r"^app\s*=\s*Flask\(__name__\)\s*$", code, flags=re.MULTILINE)
    if app_def:
        insert_at = app_def.end()
        code = code[:insert_at] + bootstrap_block + code[insert_at:]
    else:
        code = bootstrap_block + "\n" + code

    return code

def enforce_backend_cors_and_json_safety(code: str) -> str:
    if not code:
        return code

    code = code.replace("Flask_CORS", "")
    code = ensure_flask_imports(code)
    code = re.sub(r"request\.json\.get\(", r"(request.get_json(silent=True) or {}).get(", code)

    code = re.sub(r"^from flask_cors import CORS\s*$\n?", "", code, flags=re.MULTILINE)
    code = re.sub(r"^CORS\(app[^)]*\)\s*$\n?", "", code, flags=re.MULTILINE)
    code = normalize_static_cors_origin_assignment(code)

    if "Access-Control-Allow-Origin" not in code and "@app.after_request" not in code:
        app_def = re.search(r"^app\s*=\s*Flask\(__name__\)\s*$", code, flags=re.MULTILINE)
        cors_line = (
            "\nALLOWED_ORIGINS = {'http://localhost:8000', 'http://127.0.0.1:8000'}\n\n"
            "@app.after_request\n"
            "def add_cors_headers(response):\n"
            "    origin = request.headers.get('Origin')\n"
            "    if origin in ALLOWED_ORIGINS:\n"
            "        response.headers['Access-Control-Allow-Origin'] = origin\n"
            "        response.headers['Vary'] = 'Origin'\n"
            "    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'\n"
            "    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'\n"
            "    return response\n"
        )
        if app_def:
            insert_at = app_def.end()
            code = code[:insert_at] + cors_line + code[insert_at:]
        else:
            code = cors_line + "\n" + code

    return code

backend_code = enforce_backend_cors_and_json_safety(backend_code)
backend_code = enforce_project_db_bootstrap(backend_code)

def is_identifier_present(text: str, token: str) -> bool:
    if not text or not token:
        return False
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])"
    return re.search(pattern, text) is not None

def apply_identifier_replacements(text: str, replacements: dict) -> str:
    if not text:
        return text
    for src, dst in replacements.items():
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(src)}(?![A-Za-z0-9_])"
        text = re.sub(pattern, dst, text)
    return text

def enforce_typed_student_route(code: str) -> str:
    if not code:
        return code
    # Convert /students/<id> -> /students/<int:id> when route type is omitted
    return re.sub(
        r"(@app\.route\(\s*['\"]/students/<)(?!int:)([A-Za-z_][A-Za-z0-9_]*)(>\s*['\"])",
        r"\1int:\2\3",
        code,
    )

def enforce_active_contract_outputs(backend: str, html: str, css: str, js: str, contract: dict):
    if not contract:
        return backend, html, css, js

    required = set(contract.get("required_fields", []) + contract.get("post_put_required_keys", []))
    forbidden = set(contract.get("forbidden_fields", []))

    replacements = {}
    if "rollNumber" in forbidden and "roll_number" in required:
        replacements["rollNumber"] = "roll_number"
    if "_id" in forbidden and "id" in required:
        replacements["_id"] = "id"

    backend = apply_identifier_replacements(backend, replacements)
    html = apply_identifier_replacements(html, replacements)
    js = apply_identifier_replacements(js, replacements)
    css = apply_identifier_replacements(css, replacements)

    required_backend_route_fragments = contract.get("required_backend_route_fragments", [])
    if any("/students/<int:" in frag for frag in required_backend_route_fragments):
        backend = enforce_typed_student_route(backend)

    return backend, html, css, js

def validate_contract_outputs(backend: str, html: str, js: str, contract: dict):
    if not contract:
        return []

    errors = []
    frontend = f"{html}\n{js}"

    for token in contract.get("forbidden_fields", []):
        if is_identifier_present(backend, token):
            errors.append(f"Forbidden identifier '{token}' found in backend output")
        if is_identifier_present(frontend, token):
            errors.append(f"Forbidden identifier '{token}' found in frontend output")

    for field in contract.get("required_fields", []):
        if not is_identifier_present(backend, field):
            errors.append(f"Required backend field '{field}' not detected")

    for key in contract.get("post_put_required_keys", []):
        if not is_identifier_present(frontend, key):
            errors.append(f"Required frontend payload key '{key}' not detected")

    for route_fragment in contract.get("required_backend_route_fragments", []):
        if route_fragment not in backend:
            errors.append(f"Required backend route fragment '{route_fragment}' missing")

    for route_fragment in contract.get("required_frontend_path_fragments", []):
        if route_fragment not in frontend:
            errors.append(f"Required frontend route fragment '{route_fragment}' missing")

    api_base = contract.get("api_base")
    if api_base and api_base not in frontend:
        errors.append(f"Required frontend API base '{api_base}' missing")

    return sorted(set(errors))

def validate_project_db_wiring(backend: str):
    errors = []
    if "from core.db_config import DB_PATH" not in backend:
        errors.append("DB_PATH import missing in backend output")
    if re.search(r"^DB_PATH\s*=", backend, flags=re.MULTILINE):
        errors.append("Backend overrides DB_PATH directly; must import from core.db_config")
    if "sqlite3.connect(DB_PATH" not in backend:
        errors.append("sqlite3.connect(DB_PATH, ...) missing in backend output")
    has_safe_parent = (
        "_db_parent = os.path.dirname(os.path.abspath(DB_PATH)) or '.'" in backend
        and "os.makedirs(_db_parent, exist_ok=True)" in backend
    )
    has_legacy_parent = "os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)" in backend
    if not (has_safe_parent or has_legacy_parent):
        errors.append("DB parent directory creation missing in backend output")
    return sorted(set(errors))

backend_code, html_code, css_code, js_code = enforce_active_contract_outputs(
    backend_code, html_code, css_code, js_code, active_contract
)

missing_sections = []
if not backend_code:
    missing_sections.append("backend")
if not html_code:
    missing_sections.append("frontend-html")
if not css_code:
    missing_sections.append("frontend-css")
if not js_code:
    missing_sections.append("frontend-js")

if missing_sections:
    os.makedirs("output", exist_ok=True)
    with open("output/fullstack_raw_output.txt", "w", encoding="utf-8") as f:
        f.write(reply or "")
    print(f"❌ Missing required code sections in model output: {', '.join(missing_sections)}")
    print("📝 Raw model output saved to output/fullstack_raw_output.txt")
    sys.exit(1)

contract_errors = validate_contract_outputs(backend_code, html_code, js_code, active_contract)
if contract_errors:
    os.makedirs("output", exist_ok=True)
    with open("output/fullstack_raw_output.txt", "w", encoding="utf-8") as f:
        f.write(reply or "")
    print("❌ Contract validation failed after post-processing:")
    for err in contract_errors:
        print(f"   - {err}")
    print("📝 Raw model output saved to output/fullstack_raw_output.txt")
    sys.exit(1)

db_wiring_errors = validate_project_db_wiring(backend_code)
if db_wiring_errors:
    os.makedirs("output", exist_ok=True)
    with open("output/fullstack_raw_output.txt", "w", encoding="utf-8") as f:
        f.write(reply or "")
    print("❌ Project DB wiring validation failed:")
    for err in db_wiring_errors:
        print(f"   - {err}")
    print("📝 Raw model output saved to output/fullstack_raw_output.txt")
    sys.exit(1)

# ===== Save files =====
os.makedirs("output/backend", exist_ok=True)
os.makedirs("output/frontend/preview", exist_ok=True)

with open("output/backend/app.py", "w", encoding="utf-8") as f:
    f.write(backend_code)

with open("output/frontend/preview/index.html", "w", encoding="utf-8") as f:
    f.write(html_code)

with open("output/frontend/preview/styles.css", "w", encoding="utf-8") as f:
    f.write(css_code)

with open("output/frontend/preview/script.js", "w", encoding="utf-8") as f:
    f.write(js_code)

print("✅ Full-stack app generated successfully.")
