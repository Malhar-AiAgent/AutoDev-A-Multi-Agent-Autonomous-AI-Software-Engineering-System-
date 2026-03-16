# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import json
import re
import hashlib
from glob import glob
from groq import Groq
from dotenv import load_dotenv
from datetime import datetime

# ======================
# Load environment variables
# ======================
load_dotenv()
api_key = os.getenv("GROQ_API_KEY")

if not api_key:
    print("❌ GROQ_API_KEY missing. Check your .env file.")
    sys.exit(1)

client = Groq(api_key=api_key)
model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

print("\n🧰 Backend Agent launched autonomously...\n")

# ======================
# Directory setup
# ======================
base_output = "output"
backend_dir = os.path.join(base_output, "backend")
frontend_dir = os.path.join(base_output, "frontend")
CONTRACT_PATH = os.path.join("core", "student_api_contract.json")

os.makedirs(backend_dir, exist_ok=True)

# ======================
# Step 1: Locate latest inputs
# ======================
pm_files = sorted(glob(os.path.join(base_output, "pm_agent_chat_*.json")), reverse=True)
frontend_files = sorted(glob(os.path.join(frontend_dir, "frontend_plan_*.json")), reverse=True)

if frontend_files:
    input_source = frontend_files[0]
    source_type = "frontend"
else:
    input_source = pm_files[0] if pm_files else None
    source_type = "pm"

if not input_source:
    print("❌ No input file found (PM or Frontend output missing).")
    sys.exit(1)

print(f"📥 Using latest {source_type} plan: {input_source}\n")

# ======================
# Step 2: Load input data
# ======================
with open(input_source, "r", encoding="utf-8") as f:
    data = json.load(f)

project_idea = data.get("project_idea", "")
ai_plan = data.get("ai_plan", "")
frontend_code = ""

if source_type == "frontend":
    frontend_code = data.get("frontend_code", {}).get("html", "")

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

_, api_contract_text = load_active_api_contract(project_idea, ai_plan)
api_contract_block = f"API Contract (NON-NEGOTIABLE if provided):\n{api_contract_text}\n\n" if api_contract_text else ""

# ======================
# Step 2.5: DB config (unchanged, already correct)
# ======================
def slugify_project_name(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "project"

def write_db_config(project_title: str):
    slug = slugify_project_name(project_title)[:24]
    suffix = hashlib.sha1(project_title.encode("utf-8")).hexdigest()[:8]
    db_filename = f"{slug}_{suffix}.db"

    os.makedirs("core", exist_ok=True)
    with open("core/db_config.py", "w", encoding="utf-8") as f:
        f.write(
            "# core/db_config.py\n"
            "import os\n\n"
            "BASE_DIR = os.path.dirname(os.path.abspath(__file__))\n"
            "# NOTE: backend directory location is enforced by Auto_Dev pipeline\n"
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

write_db_config(project_idea)

# ======================
# Step 3: Prompt 
# ======================

messages = [
    {
        "role": "system",
        "content": (
            "You are a backend engineer in an autonomous AI development pipeline.\n\n"

            "Your task is to generate SIMPLE, STABLE, and DETERMINISTIC Flask backend code.\n\n"

            "=============================\n"
            "CRITICAL PROJECT STRUCTURE RULES (NON-NEGOTIABLE)\n"
            "=============================\n"

            "1. The backend application will be executed from the path:\n"
            "   output/backend/app.py\n\n"

            "2. Shared project modules (such as core/) exist at the PROJECT ROOT level.\n\n"

            "3. You MUST ensure Python can import shared modules by explicitly adding\n"
            "   the project root to sys.path at runtime.\n\n"

            "4. At the VERY TOP of app.py (before any other imports), you MUST include:\n\n"

            "   import os\n"
            "   import sys\n\n"
            "   ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))\n"
            "   if ROOT_DIR not in sys.path:\n"
            "       sys.path.append(ROOT_DIR)\n\n"

            "=============================\n"
            "SQLITE DATABASE RULES (NON-NEGOTIABLE)\n"
            "=============================\n"

            "5. If the project requires persistence, you MUST use SQLite.\n\n"

            "6. You MUST import the database path ONLY using:\n"
            "   from core.db_config import DB_PATH\n\n"

            "7. NEVER hardcode database filenames.\n"
            "   (Forbidden examples: 'app.db', 'students.db', 'data.db')\n\n"

            "8. NEVER use relative paths when connecting to SQLite.\n\n"

            "9. ALWAYS connect to SQLite using:\n"
            "   sqlite3.connect(DB_PATH)\n\n"

            "9.1. Use a connection timeout and safe pragmas for sync folders (OneDrive, etc.):\n"
            "   sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)\n"
            "   PRAGMA journal_mode=MEMORY\n"
            "   PRAGMA synchronous=NORMAL\n\n"

            "10. You MUST explicitly import sqlite3 when SQLite is used.\n\n"

            "11. BEFORE calling sqlite3.connect(DB_PATH), you MUST ensure\n"
            "    the parent directory of DB_PATH exists by generating code equivalent to:\n\n"
            "    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)\n\n"

            "12. Database initialization MUST run ONCE at app startup.\n\n"

            "13. NEVER assume tables already exist.\n\n"

            "14. ALWAYS create tables using:\n"
            "    CREATE TABLE IF NOT EXISTS ...\n\n"

            "15. NEVER delete, reset, rebuild, migrate, or auto-repair the database.\n\n"

            "16. Do NOT add database viewers, debug routes, seed resets,\n"
            "    or schema-altering logic beyond CREATE TABLE IF NOT EXISTS.\n\n"

            "17. The backend exclusively owns the database lifecycle.\n\n"

            "=============================\n"
            "APPLICATION RULES\n"
            "=============================\n"

            "- Backend exposes APIs only.\n"
            "- No UI, no Streamlit, no frontend logic.\n"
            "- No auto-fix, self-healing, or tester-driven behavior.\n\n"
            "- If the provided Frontend Snippet calls specific API paths,\n"
            "  you MUST implement those exact paths (even if the PM plan differs).\n\n"

            "=============================\n"
            "DEPENDENCIES\n"
            "=============================\n"

            "- Use Flask and Python standard library only.\n"
            "- Use flask-cors ONLY if explicitly required by the frontend.\n"
            "- If the frontend uses browser fetch() to call the API on a different port,\n"
            "  you MUST add basic CORS headers via @app.after_request and handle OPTIONS.\n"
            "- If the frontend references http://localhost:8000 or http://127.0.0.1:8000\n"
            "  and the backend runs on a different port (e.g., 5000), you MUST implement\n"
            "  a simple, explicit CORS allowlist for those origins and add OPTIONS routes.\n"
            "- If CORS is required, also support an env-flag for local dev to allow all\n"
            "  origins (e.g., CORS_DEV_ALLOW_ALL=1) while keeping a safe allowlist default.\n"
            "- Do NOT introduce new libraries unless absolutely necessary.\n\n"

            "REQUEST VALIDATION (MANDATORY):\n"
            "- For JSON endpoints, parse input with request.get_json(silent=True).\n"
            "- NEVER assume required fields exist in request JSON.\n"
            "- Validate required fields and return 400 with a clear JSON error message.\n"
            "- NEVER allow missing JSON keys to raise uncaught KeyError.\n\n"

            "API CONTRACT CONSISTENCY (MANDATORY):\n"
            "- If an API contract block is provided, endpoint paths and JSON keys are non-negotiable.\n"
            "- Keep request and response field names exactly as specified.\n"
            "- Do NOT mix camelCase and snake_case for contract-defined fields.\n\n"

            "=============================\n"
            "OUTPUT FORMAT RULES\n"
            "=============================\n"

            "- Output ONLY valid Python code.\n"
            "- Wrap the entire output in ONE ```python code block.\n"
            "- Do NOT include explanations or markdown outside the code block.\n"
            "- Include a top comment listing dependencies, for example:\n"
            "  # Dependencies: flask, flask-cors, sqlite3\n\n"

            "=============================\n"
            "FAILURE CONDITIONS (STRICTLY FORBIDDEN)\n"
            "=============================\n"

            "- Do NOT omit sys.path setup.\n"
            "- Do NOT hardcode .db filenames.\n"
            "- Do NOT use relative SQLite paths.\n"
            "- Do NOT call sqlite3.connect(DB_PATH) without first ensuring\n"
            "  the parent directory exists.\n"
            "- Do NOT call fetchone()/fetchall() on a sqlite3.Connection object.\n"
            "  Use conn.execute(...).fetchone() or cursor.fetchone().\n"
            "- Do NOT access request JSON using direct indexing (data['field'])\n"
            "  without required-field validation.\n"
            "- Do NOT use sqlite connections that can fail across request threads.\n"
            "  If a connection may be reused, it MUST set check_same_thread=False.\n"
            "- Do NOT delete or recreate databases.\n"
            "- Do NOT include testing, frontend, or Streamlit code.\n\n"

            "Generate clean, production-style Flask backend code that follows ALL rules above strictly."
        ),
    },
    {
        "role": "user",
        "content": (
            f"Project Idea:\n{project_idea}\n\n"
            f"PM Plan:\n{ai_plan}\n\n"
            f"Frontend Snippet:\n{frontend_code[:2000]}\n\n"
            f"{api_contract_block}"
            "Generate the backend Flask code implementing APIs, models, and logic accordingly."
        ),
    },
]



# ======================
# Step 4: Generate backend code
# ======================
try:
    print("🧠 Requesting backend code from Groq model...")
    response = client.chat.completions.create(
        model=model_name,
        messages=messages,
    )

    raw_reply = response.choices[0].message.content or ""
except Exception as e:
    print(f"⚠️ Error contacting Groq API: {e}")
    exit()

# ======================
# Step 5: Extract Python code
# ======================
pattern = r"```python\n(.*?)```"
match = re.search(pattern, raw_reply, re.DOTALL | re.IGNORECASE)
clean_code = match.group(1).strip() if match else raw_reply.strip()

# ======================
# Step 5.5: Enforce canonical header (imports + ROOT_DIR + DB_PATH)
# ======================
def enforce_canonical_header(code: str) -> str:
    if not code:
        return code

    # Strip any existing ROOT_DIR block and DB_PATH import
    code = re.sub(
        r"ROOT_DIR = os\.path\.abspath\([^\n]+\)\nif ROOT_DIR not in sys\.path:\n\s+sys\.path\.append\(ROOT_DIR\)\n",
        "",
        code,
        flags=re.MULTILINE,
    )
    code = re.sub(r"^from core\.db_config import DB_PATH\s*$\n?", "", code, flags=re.MULTILINE)

    header = (
        "import os\n"
        "import sys\n"
        "ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))\n"
        "if ROOT_DIR not in sys.path:\n"
        "    sys.path.append(ROOT_DIR)\n"
        "from core.db_config import DB_PATH\n"
    )

    lines = code.splitlines()
    # Remove duplicate import os/sys at top to avoid clutter
    filtered = []
    for l in lines:
        if l.strip() in ["import os", "import sys"]:
            continue
        filtered.append(l)
    code = "\n".join(filtered).lstrip()

    return header + code

clean_code = enforce_canonical_header(clean_code)

# ======================
# Step 5.6: Enforce basic CORS when frontend uses different port
# ======================
def enforce_basic_cors(code: str, frontend_snippet: str) -> str:
    if not code:
        return code

    # Preview always runs frontend on :8000 and backend on :5000.
    # Enforce CORS by default so PM-only runs (without frontend snippet)
    # still work in the browser.
    needs_cors = True
    if frontend_snippet:
        if re.search(r"fetch\s*\(", frontend_snippet):
            if re.search(r"(localhost|127\.0\.0\.1):8000", frontend_snippet):
                needs_cors = True
            if re.search(r"(localhost|127\.0\.0\.1):5000", frontend_snippet):
                needs_cors = True
            if re.search(r"https?://", frontend_snippet):
                needs_cors = True

    if not needs_cors:
        return code

    # Skip if CORS already present
    if re.search(r"Access-Control-Allow-Origin|@app\.after_request|CORS\(", code):
        return code

    cors_block = (
        "\n# Basic CORS for local dev frontend\n"
        "CORS_DEV_ALLOW_ALL = os.getenv(\"CORS_DEV_ALLOW_ALL\") == \"1\"\n"
        "ALLOWED_ORIGINS = {\n"
        "    \"http://localhost:8000\",\n"
        "    \"http://127.0.0.1:8000\",\n"
        "    \"http://localhost:3000\",\n"
        "    \"http://127.0.0.1:3000\",\n"
        "}\n\n"
        "@app.after_request\n"
        "def add_cors_headers(response):\n"
        "    origin = request.headers.get(\"Origin\")\n"
        "    if origin and (CORS_DEV_ALLOW_ALL or origin in ALLOWED_ORIGINS):\n"
        "        response.headers[\"Access-Control-Allow-Origin\"] = origin\n"
        "        response.headers[\"Vary\"] = \"Origin\"\n"
        "    response.headers[\"Access-Control-Allow-Methods\"] = \"GET, POST, PUT, DELETE, OPTIONS\"\n"
        "    response.headers[\"Access-Control-Allow-Headers\"] = \"Content-Type\"\n"
        "    return response\n\n"
        "@app.route('/', methods=['OPTIONS'])\n"
        "@app.route('/<path:path>', methods=['OPTIONS'])\n"
        "def options_preflight(path=None):\n"
        "    return (\"\", 204)\n"
    )

    # Insert CORS block right after app = Flask(__name__)
    match = re.search(r"^app\s*=\s*Flask\(__name__\)\s*$", code, flags=re.MULTILINE)
    if match:
        insert_at = match.end()
        code = code[:insert_at] + cors_block + code[insert_at:]
    else:
        code = cors_block + "\n" + code

    # Ensure request is imported from flask when CORS is injected
    def _ensure_request_import(match):
        imports = [p.strip() for p in match.group(1).split(",")]
        if "request" in imports:
            return match.group(0)
        return "from flask import " + ", ".join(imports + ["request"])

    if re.search(r"^from flask import [^\n]+$", code, flags=re.MULTILINE):
        code = re.sub(
            r"^from flask import ([^\n]+)$",
            _ensure_request_import,
            code,
            count=1,
            flags=re.MULTILINE,
        )

    return code

clean_code = enforce_basic_cors(clean_code, frontend_code[:2000] if frontend_code else "")

# ======================
# Step 5.7: Enforce SQLite stability patterns
# ======================
def enforce_sqlite_stability(code: str) -> str:
    if not code:
        return code

    def normalize_connect_args(match):
        args = match.group(1).strip()
        if "timeout=" not in args:
            args = f"{args}, timeout=30"
        if "check_same_thread=" not in args:
            args = f"{args}, check_same_thread=False"
        return f"sqlite3.connect({args})"

    code = re.sub(
        r"sqlite3\.connect\(([^)]*)\)",
        normalize_connect_args,
        code,
    )

    if "PRAGMA journal_mode=MEMORY" not in code:
        code = re.sub(
            r"(conn\s*=\s*sqlite3\.connect\([^)]*\))",
            (
                "\\1\n"
                "    conn.execute(\"PRAGMA journal_mode=MEMORY\")\n"
                "    conn.execute(\"PRAGMA synchronous=NORMAL\")"
            ),
            code,
            count=1,
            flags=re.MULTILINE,
        )

    buggy_snippet = (
        "conn.execute('SELECT * FROM categories WHERE category = ?', (category,))\n"
        "    category_row = conn.fetchone()"
    )
    fixed_snippet = (
        "category_row = conn.execute(\n"
        "        'SELECT total_spending FROM categories WHERE category = ?',\n"
        "        (category,)\n"
        "    ).fetchone()"
    )
    code = code.replace(buggy_snippet, fixed_snippet)

    return code

clean_code = enforce_sqlite_stability(clean_code)

# ======================
# Step 5.8: Enforce request payload safety
# ======================
def ensure_imports_for_safety(code: str) -> str:
    if not code:
        return code

    if "from flask import " in code and "jsonify" not in code:
        code = re.sub(
            r"^from flask import ([^\n]+)$",
            lambda m: "from flask import " + ", ".join(
                list(dict.fromkeys([p.strip() for p in m.group(1).split(",")] + ["jsonify"]))
            ),
            code,
            count=1,
            flags=re.MULTILINE,
        )
    return code

def enforce_request_payload_safety(code: str) -> str:
    if not code:
        return code

    code = ensure_imports_for_safety(code)

    # If route handlers still use direct JSON indexing, add global handlers to
    # prevent 500s and return API-friendly 400 responses instead.
    uses_direct_json_indexing = re.search(r"data\[['\"][^'\"]+['\"]\]", code) is not None
    if uses_direct_json_indexing and "@app.errorhandler(KeyError)" not in code:
        has_sqlite = ("import sqlite3" in code) or ("sqlite3." in code)
        safety_block = (
            "\n@app.errorhandler(KeyError)\n"
            "def handle_missing_json_key(err):\n"
            "    missing = err.args[0] if err.args else 'unknown'\n"
            "    return jsonify({'error': f'Missing required field: {missing}'}), 400\n"
        )
        if has_sqlite:
            safety_block += (
                "\n@app.errorhandler(sqlite3.IntegrityError)\n"
                "def handle_integrity_error(_err):\n"
                "    return jsonify({'error': 'Invalid or missing required fields'}), 400\n"
            )

        match = re.search(r"^app\s*=\s*Flask\(__name__\)\s*$", code, flags=re.MULTILINE)
        if match:
            insert_at = match.end()
            code = code[:insert_at] + safety_block + code[insert_at:]
        else:
            code = safety_block + "\n" + code

    return code

clean_code = enforce_request_payload_safety(clean_code)

# ======================
# Step 5.9: Enforce frontend runtime error collector endpoint
# ======================
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

def ensure_flask_imports(code: str, required_names):
    if not code:
        return code

    match = re.search(r"^from flask import ([^\n]+)$", code, flags=re.MULTILINE)
    if not match:
        fallback_names = ["Flask"] + [name for name in required_names if name != "Flask"]
        return ensure_line_in_imports(code, "from flask import " + ", ".join(fallback_names))

    existing = [p.strip() for p in match.group(1).split(",") if p.strip()]
    merged = list(dict.fromkeys(existing + [name for name in required_names if name not in existing]))
    new_line = "from flask import " + ", ".join(merged)
    return code.replace(match.group(0), new_line, 1)

def enforce_frontend_runtime_error_endpoint(code: str) -> str:
    if not code:
        return code

    if "/__frontend_errors" in code:
        return code

    code = ensure_line_in_imports(code, "import json")
    code = ensure_line_in_imports(code, "import threading")

    if not re.search(r"^from datetime import datetime\s*$", code, flags=re.MULTILINE):
        code = ensure_line_in_imports(code, "from datetime import datetime")

    code = ensure_flask_imports(code, ["request", "jsonify"])

    collector_block = (
        "\nFRONTEND_RUNTIME_ERROR_LOG = os.path.abspath(\n"
        "    os.path.join(ROOT_DIR, 'logs', 'frontend_runtime_errors.jsonl')\n"
        ")\n"
        "_frontend_runtime_error_lock = threading.Lock()\n\n"
        "def append_frontend_runtime_error(entry):\n"
        "    os.makedirs(os.path.dirname(FRONTEND_RUNTIME_ERROR_LOG), exist_ok=True)\n"
        "    with _frontend_runtime_error_lock:\n"
        "        with open(FRONTEND_RUNTIME_ERROR_LOG, 'a', encoding='utf-8') as f:\n"
        "            f.write(json.dumps(entry, ensure_ascii=True) + '\\n')\n\n"
        "@app.route('/__frontend_errors', methods=['POST', 'OPTIONS'])\n"
        "def collect_frontend_runtime_error():\n"
        "    if request.method == 'OPTIONS':\n"
        "        return ('', 204)\n\n"
        "    payload = request.get_json(silent=True)\n"
        "    if payload is None:\n"
        "        return jsonify({'error': 'Invalid request data'}), 400\n"
        "    if not isinstance(payload, dict):\n"
        "        return jsonify({'error': 'Payload must be a JSON object'}), 400\n\n"
        "    payload['received_at'] = datetime.utcnow().isoformat() + 'Z'\n"
        "    append_frontend_runtime_error(payload)\n"
        "    return jsonify({'message': 'Frontend runtime error recorded'}), 200\n"
    )

    main_guard = "if __name__ == '__main__':"
    if main_guard in code:
        code = code.replace(main_guard, collector_block + "\n" + main_guard, 1)
    else:
        code = code.rstrip() + "\n\n" + collector_block + "\n"

    return code

clean_code = enforce_frontend_runtime_error_endpoint(clean_code)

# ======================
# Step 5.10: Enforce backend runtime stability
# ======================
def enforce_backend_runtime_stability(code: str) -> str:
    if not code:
        return code

    if "sqlite3." in code and "@app.errorhandler(sqlite3.OperationalError)" not in code:
        operational_handler = (
            "\n@app.errorhandler(sqlite3.OperationalError)\n"
            "def handle_operational_error(_err):\n"
            "    return jsonify({'error': 'Database temporarily unavailable. Please retry.'}), 503\n"
        )

        if "@app.errorhandler(sqlite3.IntegrityError)" in code:
            code = code.replace(
                "@app.errorhandler(sqlite3.IntegrityError)\n"
                "def handle_integrity_error(_err):\n"
                "    return jsonify({'error': 'Invalid or missing required fields'}), 400\n",
                "@app.errorhandler(sqlite3.IntegrityError)\n"
                "def handle_integrity_error(_err):\n"
                "    return jsonify({'error': 'Invalid or missing required fields'}), 400\n"
                + operational_handler,
                1,
            )
        else:
            match = re.search(r"^app\s*=\s*Flask\(__name__\)\s*$", code, flags=re.MULTILINE)
            if match:
                insert_at = match.end()
                code = code[:insert_at] + operational_handler + code[insert_at:]
            else:
                code = operational_handler + "\n" + code

    # Prevent Flask debug reloader restarts from destabilizing tester runs.
    code = re.sub(
        r"app\.run\([^)]*\)",
        "app.run(port=5000, debug=False, use_reloader=False)",
        code,
    )

    if "__main__" in code and "app.run(" not in code:
        code = code.rstrip() + "\n\nif __name__ == '__main__':\n    app.run(port=5000, debug=False, use_reloader=False)\n"

    return code

clean_code = enforce_backend_runtime_stability(clean_code)

# ======================
# Step 6: Save backend output
# ======================
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
app_path = os.path.join(backend_dir, "app.py")
txt_path = os.path.join(backend_dir, "backend_raw_output.txt")
json_path = os.path.join(backend_dir, f"backend_plan_{timestamp}.json")

# Save main Flask app
with open(app_path, "w", encoding="utf-8") as f:
    f.write(clean_code)

# Save raw output
with open(txt_path, "w", encoding="utf-8") as f:
    f.write(raw_reply)

# Save structured metadata
backend_output = {
    "project_idea": project_idea,
    "ai_plan": ai_plan,
    "backend_code": clean_code,
    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
}

with open(json_path, "w", encoding="utf-8") as f:
    json.dump(backend_output, f, indent=4)

print(f"✅ Backend Flask app generated successfully: {app_path}")
print(f"💾 Structured backend JSON saved at: {json_path}")
print(f"📝 Raw LLM output saved for debugging.\n")
