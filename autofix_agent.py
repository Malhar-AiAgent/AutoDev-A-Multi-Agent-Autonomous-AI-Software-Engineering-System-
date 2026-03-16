# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import re
import json
from glob import glob
from datetime import datetime
from dotenv import load_dotenv
from groq import Groq


# ==========================
# Configuration
# ==========================
load_dotenv()
api_key = os.getenv("GROQ_API_KEY")
model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

if not api_key:
    print("❌ GROQ_API_KEY missing. Check your .env file.")
    sys.exit(1)

client = Groq(api_key=api_key)

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
FRONTEND_DIR = os.path.join(ROOT_DIR, "output", "frontend", "preview")
BACKEND_APP = os.path.join(ROOT_DIR, "output", "backend", "app.py")
PM_INPUT = os.path.join(ROOT_DIR, "pm_input.txt")
ERROR_FEEDBACK = os.path.join(ROOT_DIR, "error_feedback.txt")
LOG_ERROR = os.path.join(ROOT_DIR, "logs", "error_log.txt")
LOG_TESTER = os.path.join(ROOT_DIR, "logs", "tester_log.txt")
LOG_FRONTEND_RUNTIME = os.path.join(ROOT_DIR, "logs", "frontend_runtime_errors.jsonl")
AUTOFIX_LOG = os.path.join(ROOT_DIR, "logs", "autofix_runs.jsonl")

MAX_FILE_CHARS = int(os.getenv("AUTOFIX_MAX_FILE_CHARS", "20000"))
ALLOWED_FRONTEND_EXTENSIONS = {".html", ".css", ".js"}
ALLOWED_EXACT_PATHS = {
    "output/backend/app.py",
    "core/db_config.py",
}
ALLOWED_PREFIX_PATHS = {
    "output/frontend/preview/",
}


# ==========================
# Helpers
# ==========================
def read_text(path, max_chars=None):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        if max_chars and len(text) > max_chars:
            return text[-max_chars:]
        return text
    except Exception:
        return ""


def tail_text(path, max_chars=8000):
    return read_text(path, max_chars=max_chars)


def to_repo_relative(path):
    rel = os.path.relpath(path, ROOT_DIR)
    return rel.replace("\\", "/")


def is_valid_target_path(rel_path):
    rel = rel_path.strip().replace("\\", "/")
    if not rel or rel.startswith("/") or rel.startswith("../") or "/../" in rel:
        return False

    if rel in ALLOWED_EXACT_PATHS:
        return True

    for prefix in ALLOWED_PREFIX_PATHS:
        if rel.startswith(prefix):
            _, ext = os.path.splitext(rel)
            return ext.lower() in ALLOWED_FRONTEND_EXTENSIONS

    return False


def collect_project_context():
    files = []

    if os.path.exists(BACKEND_APP):
        files.append(BACKEND_APP)

    if os.path.isdir(FRONTEND_DIR):
        for fname in sorted(os.listdir(FRONTEND_DIR)):
            path = os.path.join(FRONTEND_DIR, fname)
            if os.path.isfile(path):
                _, ext = os.path.splitext(fname)
                if ext.lower() in ALLOWED_FRONTEND_EXTENSIONS:
                    files.append(path)

    snippets = []
    for path in files:
        rel = to_repo_relative(path)
        content = read_text(path, max_chars=MAX_FILE_CHARS)
        snippets.append(f"### FILE: {rel}\n{content}")

    return "\n\n".join(snippets)


def load_plan_context():
    pm_files = sorted(glob(os.path.join(ROOT_DIR, "output", "pm_agent_chat_*.json")), reverse=True)
    if not pm_files:
        return "", ""

    pm_path = pm_files[0]
    try:
        with open(pm_path, "r", encoding="utf-8", errors="ignore") as f:
            pm_data = json.load(f)
        return pm_data.get("project_idea", ""), pm_data.get("ai_plan", "")
    except Exception:
        return "", ""


def build_prompt():
    project_idea, ai_plan = load_plan_context()
    manual_input = read_text(PM_INPUT, max_chars=4000)
    error_feedback = read_text(ERROR_FEEDBACK, max_chars=12000)
    backend_log = tail_text(LOG_ERROR, max_chars=12000)
    tester_log = tail_text(LOG_TESTER, max_chars=12000)
    frontend_runtime_log = tail_text(LOG_FRONTEND_RUNTIME, max_chars=12000)
    code_context = collect_project_context()

    if not code_context:
        return None

    system_prompt = (
        "You are AutoFix Agent inside an autonomous code generation pipeline.\n"
        "You receive failing logs plus current project files and must patch only the files required.\n\n"
        "Output requirements (STRICT):\n"
        "1) Return ONLY file blocks in this exact format:\n"
        "---file:relative/path---\n"
        "<full file content>\n"
        "---endfile---\n"
        "2) Do not include explanations.\n"
        "3) Include only files that must be changed.\n"
        "4) Never invent unrelated files.\n"
        "5) Keep backend+frontend API contract aligned.\n"
        "6) Preserve existing working behavior and fix only failures.\n"
        "7) If no changes are needed, return exactly: NO_CHANGES\n\n"
        "Backend constraints:\n"
        "- Flask API in output/backend/app.py must stay runnable.\n"
        "- Use request.get_json(silent=True) and validate required fields.\n"
        "- Keep CORS support for localhost:8000.\n\n"
        "Frontend constraints:\n"
        "- JS may run on multiple pages: guard missing DOM elements.\n"
        "- Any FormData(...) call must use an actual HTMLFormElement.\n"
        "- Keep API_BASE pointing to http://127.0.0.1:5000 or /students endpoint variant already used by backend.\n"
        "- Keep IDs/names consistent with HTML."
    )

    user_prompt = (
        f"Project idea:\n{manual_input or project_idea}\n\n"
        f"PM plan:\n{ai_plan}\n\n"
        f"error_feedback.txt:\n{error_feedback}\n\n"
        f"logs/error_log.txt (tail):\n{backend_log}\n\n"
        f"logs/tester_log.txt (tail):\n{tester_log}\n\n"
        f"logs/frontend_runtime_errors.jsonl (tail):\n{frontend_runtime_log}\n\n"
        f"Current files:\n{code_context}\n\n"
        "Return patch blocks now."
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def parse_file_blocks(text):
    pattern = r"---file:(.*?)---(.*?)---endfile---"
    blocks = re.findall(pattern, text, flags=re.DOTALL)
    parsed = []
    for raw_path, content in blocks:
        rel = raw_path.strip().replace("\\", "/")
        parsed.append((rel, content.lstrip("\n")))
    return parsed

def enforce_backend_db_path_contract(content: str) -> str:
    if not content:
        return content

    content = re.sub(
        r"ROOT_DIR\s*=\s*os\.path\.abspath\([^\n]+\)\nif ROOT_DIR not in sys\.path:\n\s+sys\.path\.append\(ROOT_DIR\)\n?",
        "",
        content,
        flags=re.MULTILINE,
    )
    content = re.sub(r"^from core\.db_config import DB_PATH\s*$\n?", "", content, flags=re.MULTILINE)
    content = re.sub(r"^DB_PATH\s*=.*$\n?", "", content, flags=re.MULTILINE)

    lines = content.splitlines()
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
    content = header + ("\n" + body if body else "")

    safe_parent_block = "_db_parent = os.path.dirname(os.path.abspath(DB_PATH)) or '.'\nos.makedirs(_db_parent, exist_ok=True)"
    if "_db_parent = os.path.dirname(os.path.abspath(DB_PATH)) or '.'" not in content:
        if "os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)" in content:
            content = content.replace("os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)", safe_parent_block)
        else:
            connect_def = re.search(r"^def _autodev_connect\(\):\s*$", content, flags=re.MULTILINE)
            if connect_def:
                insert_at = connect_def.start()
                content = content[:insert_at] + safe_parent_block + "\n\n" + content[insert_at:]
            else:
                app_def = re.search(r"^app\s*=\s*Flask\(__name__\)\s*$", content, flags=re.MULTILINE)
                if app_def:
                    insert_at = app_def.end()
                    content = content[:insert_at] + "\n" + safe_parent_block + "\n" + content[insert_at:]
                else:
                    content = safe_parent_block + "\n\n" + content

    content = re.sub(r"^from flask_cors import CORS\s*$\n?", "", content, flags=re.MULTILINE)
    content = re.sub(r"^CORS\(app[^)]*\)\s*$\n?", "", content, flags=re.MULTILINE)

    flask_import = re.search(r"^from flask import ([^\n]+)$", content, flags=re.MULTILINE)
    if flask_import:
        existing = [p.strip() for p in flask_import.group(1).split(",") if p.strip()]
        merged = list(dict.fromkeys(existing + ["request"]))
        content = content.replace(flask_import.group(0), "from flask import " + ", ".join(merged), 1)

    if "Access-Control-Allow-Origin" not in content and "@app.after_request" not in content:
        cors_block = (
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
        app_def = re.search(r"^app\s*=\s*Flask\(__name__\)\s*$", content, flags=re.MULTILINE)
        if app_def:
            insert_at = app_def.end()
            content = content[:insert_at] + cors_block + content[insert_at:]
    else:
        static_origin_pattern = r"^(\s*)response\.headers\[['\"]Access-Control-Allow-Origin['\"]\]\s*=\s*['\"]https?://(?:localhost|127\.0\.0\.1):\d+['\"]\s*$"
        if re.search(static_origin_pattern, content, flags=re.MULTILINE):
            content = re.sub(
                static_origin_pattern,
                (
                    r"\1origin = request.headers.get('Origin')\n"
                    r"\1if origin in ALLOWED_ORIGINS:\n"
                    r"\1    response.headers['Access-Control-Allow-Origin'] = origin\n"
                    r"\1    response.headers['Vary'] = 'Origin'"
                ),
                content,
                flags=re.MULTILINE,
            )
            if "ALLOWED_ORIGINS = {" not in content:
                app_def = re.search(r"^app\s*=\s*Flask\(__name__\)\s*$", content, flags=re.MULTILINE)
                if app_def:
                    insert_at = app_def.end()
                    content = (
                        content[:insert_at]
                        + "\nALLOWED_ORIGINS = {'http://localhost:8000', 'http://127.0.0.1:8000'}\n"
                        + content[insert_at:]
                    )

    content = content.replace(
        "    conn.execute('PRAGMA journal_mode=MEMORY')\n"
        "    conn.execute('PRAGMA synchronous=NORMAL')",
        "    try:\n"
        "        conn.execute('PRAGMA journal_mode=MEMORY')\n"
        "        conn.execute('PRAGMA synchronous=NORMAL')\n"
        "    except sqlite3.OperationalError:\n"
        "        pass",
    )

    return content


def apply_patches(blocks):
    if not blocks:
        return []

    applied = []
    for rel_path, content in blocks:
        if not is_valid_target_path(rel_path):
            print(f"⚠️ Skipping non-allowed path from model: {rel_path}")
            continue

        abs_path = os.path.abspath(os.path.join(ROOT_DIR, rel_path))
        if not abs_path.startswith(ROOT_DIR):
            print(f"⚠️ Skipping unsafe path from model: {rel_path}")
            continue

        if rel_path == "output/backend/app.py":
            content = enforce_backend_db_path_contract(content)

        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content.rstrip() + "\n")
        applied.append(rel_path)
        print(f"✅ Patched: {rel_path}")

    return applied


def log_run(result, model_reply, applied_files):
    os.makedirs(os.path.dirname(AUTOFIX_LOG), exist_ok=True)
    record = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "result": result,
        "applied_files": applied_files,
        "model_reply_preview": model_reply[:1500],
    }
    with open(AUTOFIX_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True) + "\n")


def main():
    print("\n🛠️ AutoFix Agent launched...\n")
    messages = build_prompt()
    if not messages:
        print("⚠️ No generated frontend/backend files found to patch.")
        sys.exit(1)

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
        )
    except Exception as e:
        print(f"⚠️ AutoFix model call failed: {e}")
        sys.exit(1)

    reply = (response.choices[0].message.content or "").strip()
    if not reply:
        log_run("empty_reply", reply, [])
        print("⚠️ AutoFix returned an empty response.")
        sys.exit(1)

    if reply == "NO_CHANGES":
        log_run("no_changes", reply, [])
        print("ℹ️ AutoFix decided no changes are needed.")
        sys.exit(2)

    blocks = parse_file_blocks(reply)
    if not blocks:
        log_run("invalid_format", reply, [])
        print("⚠️ AutoFix response did not contain valid patch blocks.")
        sys.exit(1)

    applied = apply_patches(blocks)
    if not applied:
        log_run("blocked_paths", reply, [])
        print("⚠️ AutoFix produced no applicable file updates.")
        sys.exit(1)

    log_run("patched", reply, applied)
    print(f"\n✅ AutoFix applied {len(applied)} file(s).")
    sys.exit(0)


if __name__ == "__main__":
    main()
