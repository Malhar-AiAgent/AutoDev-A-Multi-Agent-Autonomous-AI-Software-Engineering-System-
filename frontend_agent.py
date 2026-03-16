# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')

import os
import json
import re
from glob import glob
from groq import Groq
from dotenv import load_dotenv
from datetime import datetime

# ===== Load Environment =====
load_dotenv()
api_key = os.getenv("GROQ_API_KEY")

if not api_key:
    print("🚨 GROQ_API_KEY missing. Check your .env file.")
    exit()

client = Groq(api_key=api_key)
model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

print("\n🧠 Frontend Agent launched autonomously...\n")

# ===== Directories =====
output_dir = "output"
frontend_dir = os.path.join(output_dir, "frontend")
preview_dir = os.path.join(frontend_dir, "preview")
os.makedirs(preview_dir, exist_ok=True)
ERROR_REPORTER_FILE = "autodev-error-reporter.js"
CONTRACT_PATH = os.path.join("core", "student_api_contract.json")

# ===== Detect latest PM Agent JSON =====
pm_files = sorted(glob(os.path.join(output_dir, "pm_agent_chat_*.json")), reverse=True)
if not pm_files:
    print("⚠️ No PM Agent output found. Please run PM Agent first.")
    exit()

pm_file = pm_files[0]
print(f"📄 Using PM plan: {pm_file}\n")

# ===== Load project plan =====
with open(pm_file, "r", encoding="utf-8") as f:
    pm_data = json.load(f)

project_idea = pm_data.get("project_idea", "")
ai_plan = pm_data.get("ai_plan", pm_data.get("summary", ""))

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

# ===== UNIVERSAL MARKDOWN STRIPPER (PERMANENT FIX) =====
def strip_markdown_fences(code: str) -> str:
    """
    Removes ```html, ```css, ```javascript, ```js and closing ```
    from generated code permanently.
    """
    if not code:
        return ""

    code = re.sub(r"```[a-zA-Z]*", "", code)
    code = code.replace("```", "")
    return code.strip()

def ensure_doctype(html: str) -> str:
    """
    Ensures the HTML file starts with <!DOCTYPE html>
    to prevent Quirks Mode.
    """
    if not html:
        return ""

    stripped = html.lstrip().lower()
    if not stripped.startswith("<!doctype html"):
        return "<!DOCTYPE html>\n" + html.lstrip()

    return html

def enforce_script_dependency_order(html: str) -> str:
    """
    Keep third-party libraries before local app scripts to avoid
    runtime reference errors (e.g., Chart before script.js).
    """
    if not html:
        return html

    pattern = r"<script\b[^>]*src=['\"][^'\"]+['\"][^>]*>\s*</script>"
    script_tags = re.findall(pattern, html, flags=re.IGNORECASE)
    if len(script_tags) < 2:
        return html

    def src_from_tag(tag: str) -> str:
        match = re.search(r"src=['\"]([^'\"]+)['\"]", tag, flags=re.IGNORECASE)
        return (match.group(1).strip() if match else "").lower()

    external = []
    local = []
    for tag in script_tags:
        src = src_from_tag(tag)
        if src.startswith("http://") or src.startswith("https://"):
            external.append(tag)
        else:
            local.append(tag)

    reordered = external + local
    if reordered == script_tags:
        return html

    cleaned_html = html
    for tag in script_tags:
        cleaned_html = cleaned_html.replace(tag, "", 1)

    injection = "\n    " + "\n    ".join(reordered) + "\n"
    if "</body>" in cleaned_html:
        return cleaned_html.replace("</body>", injection + "</body>", 1)

    return cleaned_html + injection

def normalize_html_file(path: str):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            original = f.read()
    except Exception:
        return

    normalized = ensure_doctype(original)
    normalized = enforce_script_dependency_order(normalized)

    if normalized != original:
        with open(path, "w", encoding="utf-8") as f:
            f.write(normalized)

def normalize_all_html_files(directory: str):
    for fname in os.listdir(directory):
        if fname.lower().endswith(".html"):
            normalize_html_file(os.path.join(directory, fname))

def write_error_reporter_script(directory: str):
    reporter_path = os.path.join(directory, ERROR_REPORTER_FILE)
    reporter_code = r"""(function () {
    var ENDPOINT = 'http://127.0.0.1:5000/__frontend_errors';

    function safeStringify(value) {
        try {
            if (value instanceof Error) {
                return {
                    name: value.name,
                    message: value.message,
                    stack: value.stack || null
                };
            }
            if (typeof value === 'object' && value !== null) {
                return JSON.parse(JSON.stringify(value));
            }
            return String(value);
        } catch (_err) {
            return '[unserializable]';
        }
    }

    function postFrontendError(kind, payload) {
        try {
            fetch(ENDPOINT, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    kind: kind,
                    href: window.location.href,
                    userAgent: navigator.userAgent,
                    payload: payload || {},
                    clientTimestamp: new Date().toISOString()
                })
            }).catch(function () {});
        } catch (_err) {
            // best-effort telemetry only
        }
    }

    window.addEventListener('error', function (event) {
        postFrontendError('window.error', {
            message: event && event.message ? event.message : 'Unknown error',
            source: event && event.filename ? event.filename : null,
            lineno: event && typeof event.lineno === 'number' ? event.lineno : null,
            colno: event && typeof event.colno === 'number' ? event.colno : null,
            stack: event && event.error && event.error.stack ? event.error.stack : null
        });
    });

    window.addEventListener('unhandledrejection', function (event) {
        var reason = event ? event.reason : null;
        postFrontendError('window.unhandledrejection', {
            reason: safeStringify(reason),
            stack: reason && reason.stack ? reason.stack : null
        });
    });

    var originalConsoleError = console.error;
    console.error = function () {
        var args = Array.prototype.slice.call(arguments).map(safeStringify);
        postFrontendError('console.error', { args: args });
        if (typeof originalConsoleError === 'function') {
            originalConsoleError.apply(console, arguments);
        }
    };
})();"""
    with open(reporter_path, "w", encoding="utf-8") as f:
        f.write(reporter_code.strip() + "\n")

def inject_error_reporter_into_html(directory: str):
    script_tag = f'<script src="{ERROR_REPORTER_FILE}"></script>'

    for fname in os.listdir(directory):
        if not fname.lower().endswith(".html"):
            continue

        path = os.path.join(directory, fname)
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                html = f.read()
        except Exception:
            continue

        if script_tag in html:
            continue

        if "<head>" in html:
            html = html.replace("<head>", f"<head>\n    {script_tag}", 1)
        elif "<script" in html:
            html = re.sub(r"<script", f"{script_tag}\n<script", html, count=1)
        elif "</body>" in html:
            html = html.replace("</body>", f"    {script_tag}\n</body>", 1)
        else:
            html += f"\n{script_tag}\n"

        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

# ===== Groq Prompt (OPTION 2 + PERMANENT JS FIX ENFORCED) =====
messages = [
    {
        "role": "system",
        "content": (
            "You are an autonomous frontend engineer in the Auto_Dev pipeline.\n\n"

            "CRITICAL UX RULE:\n"
            "- If the project contains MORE THAN 3 distinct features or sections,\n"
            "  you MUST generate a MULTI-PAGE frontend.\n"
            "- Do NOT place all major sections on a single page.\n\n"

            "SINGLE-PAGE is allowed ONLY for very small apps.\n\n"

            "OUTPUT FORMAT:\n"
            "- Single-page: provide raw HTML, CSS, and JavaScript code without markdown formatting.\n"
            "- Multi-page: ---file:filename.html--- ... ---endfile---\n\n"

            "====================\n"
            "CRITICAL JAVASCRIPT SAFETY RULES (NON-NEGOTIABLE):\n"
            "====================\n"

            "DOM SAFETY:\n"
            "- JavaScript may be shared across multiple pages.\n"
            "- NEVER assume a DOM element exists.\n"
            "- ALWAYS check that an element is not null before:\n"
            "  * calling addEventListener\n"
            "  * reading or setting innerHTML\n"
            "  * accessing .value, .checked, or other properties\n"
            "- Guard all DOM access using if (element) or equivalent checks.\n\n"

            "DATA SAFETY (MOST IMPORTANT):\n"
            "- NEVER assume the structure of data loaded from localStorage or APIs.\n"
            "- ALWAYS normalize data immediately after loading.\n"
            "- Any nested object MUST be initialized if missing.\n"
            "- Example (MANDATORY PATTERN):\n"
            "  habits = habits.map(habit => ({\n"
            "      ...habit,\n"
            "      completionHistory: habit.completionHistory || {}\n"
            "  }));\n"
            "- NEVER access obj[key] unless obj is guaranteed to exist.\n\n"

            "BACKEND INTEGRATION (MANDATORY WHEN API EXISTS):\n"
            "- If the PM plan lists API endpoints, you MUST integrate the UI with those APIs.\n"
            "- Do NOT keep the app purely client-side (no local-only arrays for persistence).\n"
            "- Use fetch() to call the endpoints for create/read/update/delete actions.\n"
            "- Use a single API_BASE (http://127.0.0.1:5000) and build URLs from it.\n"
            "- After each mutation (POST/PUT/DELETE), re-fetch the list from the API.\n\n"

            "API CONTRACT CONSISTENCY (MANDATORY):\n"
            "- If an API contract block is provided, use those endpoint paths and JSON keys exactly.\n"
            "- Do NOT rename fields or mix snake_case and camelCase.\n"
            "- Keep frontend payload keys and backend response keys contract-aligned.\n\n"

            "RULES:\n"
            "- No explanations.\n"
            "- Code must be runnable without runtime JavaScript errors.\n"
            "- Generated JavaScript must be safe for multi-page usage.\n"
            "- All HTML must correctly link CSS and JS.\n"
        ),
    },
    {
        "role": "user",
        "content": (
            f"Project Idea:\n{project_idea}\n\n"
            f"PM Agent Plan:\n{ai_plan}\n\n"
            f"{api_contract_block}"
            "Generate the complete frontend now."
        ),
    },
]


# ===== Request frontend code =====
print("💬 Requesting frontend code from Groq model...")
response = client.chat.completions.create(model=model_name, messages=messages)
reply = response.choices[0].message.content or ""

# ===== Extract code blocks (safe fallback) =====
def extract_code_block(text, language):
    pattern = rf"```{language}\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""

index_html = extract_code_block(reply, "html")
styles_css = extract_code_block(reply, "css")
script_js = extract_code_block(reply, "javascript") or extract_code_block(reply, "js")

if not index_html:
    index_html = reply
if not styles_css:
    styles_css = "/* No CSS provided */"
if not script_js:
    script_js = "// No JavaScript provided"

# ===== Save Frontend Files (FENCES STRIPPED HERE) =====
def save_frontend_output(frontend_code, html_code, css_code, js_code):
    file_blocks = re.findall(r"---file:(.*?)---(.*?)---endfile---", frontend_code, re.DOTALL)

    if file_blocks:
        print("🧩 Multi-page frontend detected. Writing files...\n")
        for filename, content in file_blocks:
            path = os.path.join(preview_dir, filename.strip())
            clean_content = strip_markdown_fences(content)
            if filename.strip().lower().endswith(".html"):
                clean_content = ensure_doctype(clean_content)
                clean_content = enforce_script_dependency_order(clean_content)
            with open(path, "w", encoding="utf-8") as f:
                f.write(clean_content)
            print(f"✅ Created: {filename.strip()}")
    else:
        with open(os.path.join(preview_dir, "index.html"), "w", encoding="utf-8") as f:
            html = strip_markdown_fences(html_code)
            html = ensure_doctype(html)
            html = enforce_script_dependency_order(html)
            f.write(html)
        with open(os.path.join(preview_dir, "styles.css"), "w", encoding="utf-8") as f:
            f.write(strip_markdown_fences(css_code))
        with open(os.path.join(preview_dir, "script.js"), "w", encoding="utf-8") as f:
            f.write(strip_markdown_fences(js_code))
        print("✅ Single-page frontend created.")

save_frontend_output(reply, index_html, styles_css, script_js)
normalize_all_html_files(preview_dir)
write_error_reporter_script(preview_dir)
inject_error_reporter_into_html(preview_dir)

# ===== DOM id consistency check (auto-fix) =====
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

script_path = os.path.join(preview_dir, "script.js")
try:
    with open(script_path, "r", encoding="utf-8", errors="ignore") as f:
        js_text = f.read()
except Exception:
    js_text = ""

html_ids = collect_html_ids(preview_dir)
js_ids = collect_js_ids(js_text)
missing_ids = sorted([i for i in js_ids if i not in html_ids])

if missing_ids:
    print(f"⚠️ JS references missing HTML IDs: {missing_ids}. Attempting auto-fix...")
    fix_messages = [
        {
            "role": "system",
            "content": (
                "You are a frontend engineer. Return ONLY JavaScript code (no markdown).\n"
                "Use ONLY the provided HTML element IDs. Guard DOM access.\n"
                "Do NOT invent new IDs.\n"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Available HTML IDs: {sorted(html_ids)}\n\n"
                f"Project Plan:\n{ai_plan}\n\n"
                f"{api_contract_block}"
                "Rewrite script.js to use only these IDs."
            ),
        },
    ]
    try:
        fix_response = client.chat.completions.create(model=model_name, messages=fix_messages)
        fix_js = (fix_response.choices[0].message.content or "").strip()
        if fix_js:
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(strip_markdown_fences(fix_js))
            print("✅ script.js auto-fixed for DOM IDs.")
    except Exception as e:
        print(f"⚠️ DOM auto-fix failed: {e}")

# ===== Post-generation validation + auto-fix for API integration =====
def requires_api_integration(plan_text: str) -> bool:
    if not plan_text:
        return False
    low = plan_text.lower()
    return any(k in low for k in ["api", "endpoint", "backend", "flask", "server"])

def script_uses_fetch(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return "fetch(" in f.read()
    except Exception:
        return False

def script_uses_local_storage(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return "localStorage" in f.read()
    except Exception:
        return False

def normalize_api_base_for_local_preview(path: str):
    """
    Ensure absolute local API base points to backend port 5000.
    """
    js = read_text(path)
    if not js:
        return

    pattern = r"(\b(?:const|let|var)\s+API_BASE\s*=\s*['\"])(https?://(?:localhost|127\.0\.0\.1):)(\d+)([^'\"]*)(['\"])"

    def replacer(match):
        prefix, host_part, port, suffix, quote = match.groups()
        if port == "5000":
            return match.group(0)
        return f"{prefix}{host_part}5000{suffix}{quote}"

    normalized = re.sub(pattern, replacer, js)
    if normalized != js:
        with open(path, "w", encoding="utf-8") as f:
            f.write(normalized)

def apply_api_integration_fix(reason: str) -> bool:
    print(f"⚠️ {reason}. Attempting auto-fix...")
    available_ids = sorted(collect_html_ids(preview_dir))
    fix_messages = [
        {
            "role": "system",
            "content": (
                "You are a frontend engineer. Return ONLY JavaScript code (no markdown, no explanations).\n"
                "The JS MUST integrate the backend API using fetch().\n"
                "Use API_BASE = 'http://127.0.0.1:5000'.\n"
                "Do NOT use localStorage for persistence.\n"
                "The final code MUST contain literal fetch(...) calls.\n"
                "Wire the existing HTML form and list elements to the API.\n"
                "Guard DOM access for multi-page usage.\n"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Available HTML IDs: {available_ids}\n\n"
                f"Project Plan:\n{ai_plan}\n\n"
                f"{api_contract_block}"
                "Generate a robust script.js that calls the backend endpoints described."
            ),
        },
    ]

    try:
        fix_response = client.chat.completions.create(model=model_name, messages=fix_messages)
        fix_js = (fix_response.choices[0].message.content or "").strip()
        if fix_js:
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(strip_markdown_fences(fix_js))
            print("✅ script.js auto-fixed with API integration.")
            return True
    except Exception as e:
        print(f"⚠️ API integration auto-fix failed: {e}")
    return False

script_path = os.path.join(preview_dir, "script.js")
api_required = requires_api_integration(ai_plan)
if api_required and (not script_uses_fetch(script_path) or script_uses_local_storage(script_path)):
    apply_api_integration_fix("Frontend JS missing API integration or still uses localStorage")

# ===== Post-generation validation + auto-fix for JS safety =====
def read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

def has_direct_dom_listener(js_text: str) -> bool:
    """
    Catch unsafe pattern like:
    document.getElementById('x').addEventListener(...)
    """
    pattern = r"document\.getElementById\(['\"][^'\"]+['\"]\)\.addEventListener\("
    return re.search(pattern, js_text) is not None

def has_untyped_numeric_inputs(js_text: str) -> bool:
    """
    Catch probable numeric inputs assigned from .value without Number/parse*.
    """
    numeric_hints = ("amount", "price", "cost", "qty", "quantity", "total")
    pattern = r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*([^;\n]+);"
    for var_name, expr in re.findall(pattern, js_text):
        name = var_name.lower()
        if any(hint in name for hint in numeric_hints) and ".value" in expr:
            expr_low = expr.lower()
            if "parsefloat(" not in expr_low and "parseint(" not in expr_low and "number(" not in expr_low:
                return True
    return False

script_text = read_text(script_path)
needs_dom_guard_fix = has_direct_dom_listener(script_text)
needs_numeric_fix = has_untyped_numeric_inputs(script_text)

if needs_dom_guard_fix or needs_numeric_fix:
    reasons = []
    if needs_dom_guard_fix:
        reasons.append("unsafe direct addEventListener on getElementById")
    if needs_numeric_fix:
        reasons.append("numeric form values not normalized before API payload")
    print(f"⚠️ Frontend JS safety checks failed ({', '.join(reasons)}). Attempting auto-fix...")

    available_ids = sorted(collect_html_ids(preview_dir))
    fix_messages = [
        {
            "role": "system",
            "content": (
                "You are a frontend engineer. Return ONLY JavaScript code (no markdown).\n"
                "Mandatory fixes:\n"
                "- Guard all DOM lookups before use.\n"
                "- Never use document.getElementById(...).addEventListener(...) directly.\n"
                "- Normalize numeric form inputs using Number.parseFloat/parseInt before API payloads.\n"
                "- Keep API_BASE and fetch paths stable unless obviously broken.\n"
                "- Keep code runnable across multi-page HTML files.\n"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Available HTML IDs: {available_ids}\n\n"
                f"Project Plan:\n{ai_plan}\n\n"
                f"{api_contract_block}"
                "Rewrite script.js to satisfy all mandatory fixes."
            ),
        },
    ]

    try:
        fix_response = client.chat.completions.create(model=model_name, messages=fix_messages)
        fix_js = (fix_response.choices[0].message.content or "").strip()
        if fix_js:
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(strip_markdown_fences(fix_js))
            print("✅ script.js auto-fixed for DOM safety and numeric normalization.")
    except Exception as e:
        print(f"⚠️ JS safety auto-fix failed: {e}")

# Final API integrity pass: safety rewrites must not remove backend integration.
if api_required and (not script_uses_fetch(script_path) or script_uses_local_storage(script_path)):
    apply_api_integration_fix("Frontend JS lost API integration after safety fixes")

if api_required:
    normalize_api_base_for_local_preview(script_path)

normalize_all_html_files(preview_dir)
inject_error_reporter_into_html(preview_dir)

print("\n🏁 Frontend generation completed successfully.")
print(f"📁 Preview available in: {preview_dir}")
