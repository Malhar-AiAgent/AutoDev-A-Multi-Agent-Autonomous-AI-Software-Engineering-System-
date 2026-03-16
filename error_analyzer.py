# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")

import re
import time
import json
import os


class ErrorAnalyzer:
    """
    Read-only error classifier.
    Detects backend/runtime errors and forwards them to the PM Agent.
    NEVER mutates code. NEVER installs dependencies.
    """

    def __init__(self, log_file="logs/error_log.txt"):
        self.log_file = log_file

    # ==============================
    # 🔍 Error Classification
    # ==============================
    def detect_error_type(self, log_text: str) -> str:
        patterns = {
            "CORS_ERROR": r"CORS policy|Access-Control-Allow-Origin",
            "FETCH_ERROR": r"TypeError: Failed to fetch|Invalid JSON",
            "IMPORT_ERROR": r"ModuleNotFoundError|ImportError",
            "SYNTAX_ERROR": r"SyntaxError",
            "ROUTE_404": r"404 Not Found|route not found",
            "RUNTIME_ERROR": r"Traceback|Exception",
        }

        for error_type, pattern in patterns.items():
            if re.search(pattern, log_text, re.IGNORECASE):
                return error_type

        return "UNKNOWN_ERROR"

    # ==============================
    # 📤 Public Interface
    # ==============================
    def analyze_logs(self):
        if not os.path.exists(self.log_file):
            return None

        with open(self.log_file, "r", encoding="utf-8", errors="ignore") as f:
            log_text = f.read()

        return self.detect_error_type(log_text)

    def handle_error(self, error_type: str):
        """
        Forward error to PM Agent.
        NO auto-fix. NO backend modification.
        """
        print(f"\n🧩 Detected issue type: {error_type}")
        self.send_feedback_to_pm(error_type)

    # ==============================
    # 🧾 Feedback Channel
    # ==============================
    def send_feedback_to_pm(self, error_type: str):
        feedback = {
            "stage": "tester",
            "status": "failed",
            "error_type": error_type,
            "timestamp": time.time(),
            "policy": "read-only"
        }

        os.makedirs("logs", exist_ok=True)
        with open("logs/error_feedback.json", "w", encoding="utf-8") as f:
            json.dump(feedback, f, indent=4)

        print("📨 Error forwarded to PM Agent (no auto-fix applied).")
