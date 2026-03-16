# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')

from groq import Groq
import json
from datetime import datetime
from dotenv import load_dotenv
import os

# ==========================
# ⚙️ Setup
# ==========================
load_dotenv()
api_key = os.getenv("GROQ_API_KEY")

if not api_key:
    print("❌ GROQ_API_KEY missing in .env file.")
    exit()

client = Groq(api_key=api_key)

print("\n🤖 PM Agent launched autonomously...\n")

# ==========================
# 🧠 Load feedback context
# ==========================
feedback_context = ""

def dedupe_lines(text: str) -> str:
    seen = set()
    ordered = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(line)
    return "\n".join(ordered)

# Load manual feedback if exists
if os.path.exists("feedback.txt"):
    with open("feedback.txt", "r", encoding="utf-8") as f:
        feedback_context += dedupe_lines(f.read().strip())
    os.remove("feedback.txt")

# Load testing feedback if exists
if os.path.exists("error_feedback.txt"):
    with open("error_feedback.txt", "r", encoding="utf-8") as f:
        deduped_error_feedback = dedupe_lines(f.read().strip())
        if deduped_error_feedback:
            feedback_context += "\n\nErrors Detected During Testing:\n" + deduped_error_feedback
    os.remove("error_feedback.txt")

if feedback_context:
    print("🧾 Loaded feedback context for this session:\n")
    print(feedback_context + "\n")

# ==========================
# 📥 Load user project idea
# ==========================
if not os.path.exists("pm_input.txt"):
    print("❌ No pm_input.txt found! Please provide input from Streamlit UI.")
    exit()

with open("pm_input.txt", "r", encoding="utf-8", errors="ignore") as f:
    project_idea = f.read().strip()

print(f"💬 Project idea received from Streamlit UI:\n{project_idea}\n")

# ==========================
# 🧩 Prepare conversation
# ==========================
feedback_text = ""
if feedback_context:
    feedback_text = "Feedback and Errors:\n" + feedback_context

messages = [
    {
        "role": "system",
        "content": (
            "You are the PM Agent of the Auto_Dev system. "
            "Your task is to analyze the user's project idea and produce clear, actionable specifications "
            "for the Frontend, Backend, and Tester agents. "
            "If feedback or error logs exist, use them to refine your planning."
        ),
    },
    {
        "role": "user",
        "content": (
            f"User Project Idea:\n{project_idea}\n\n"
            f"{feedback_text}"
        ),
    },
]

# ==========================
# 🧠 Generate structured project plan
# ==========================
try:
    print("🧠 Generating project plan using Groq model...")

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
    )

    reply = response.choices[0].message.content.strip()

    print("\n✅ PM Agent completed successfully!\n")
    print("🪄 Here's the generated plan (preview):\n")
    print(reply[:1000] + ("..." if len(reply) > 1000 else ""))

    # ==========================
    # 💾 Save output
    # ==========================
    output_data = {
        "project_idea": project_idea,
        "feedback_context": feedback_context,
        "ai_plan": reply,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    os.makedirs("output", exist_ok=True)
    filename = f"output/pm_agent_chat_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=4)

    print(f"\n💾 Plan saved successfully at: {filename}\n")

except Exception as e:
    print(f"⚠️ Error connecting to Groq API: {e}")
