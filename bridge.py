#!/usr/bin/env python3
"""Bridge OpenClaw agent activity → Star-Office-UI state.json

Polls OpenClaw logs every few seconds and maps agent activity to pixel office states:
  idle        → Rest Area (no active runs)
  writing     → Work Area (agent generating response)
  executing   → Work Area (agent running tools)
  syncing     → Work Area (cron job running)
  error       → Bug Area (error detected)
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime

STATE_FILE = "/data/star-office/state.json"
POLL_INTERVAL = 4  # seconds


def write_state(state, detail=""):
    data = {
        "state": state,
        "detail": detail[:200] if detail else "",
        "progress": 0,
        "updated_at": datetime.now().isoformat()
    }
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print(f"[bridge] write failed: {e}", flush=True)


def get_logs():
    """Get recent openclaw logs."""
    try:
        r = subprocess.run(
            ["openclaw", "logs", "--max-bytes", "8000"],
            capture_output=True, text=True, timeout=5
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def parse_state(logs):
    """Parse logs to determine current agent state."""
    lines = logs.strip().split("\n")

    # Find the latest totalActive count
    total_active = 0
    last_channel = ""
    last_tool = ""
    has_error = False
    has_cron = False

    for line in lines:
        # totalActive from diagnostics
        m = re.search(r'totalActive=(\d+)', line)
        if m:
            total_active = int(m.group(1))

        # Track channels from run starts
        m = re.search(r'embedded run start.*?messageChannel=(\S+)', line)
        if m:
            last_channel = m.group(1)

        # Track tools
        m = re.search(r'embedded run tool start.*?tool=(\S+)', line)
        if m:
            last_tool = m.group(1)

        # Cron activity
        if 'cron:' in line and ('run start' in line or 'task done' in line):
            has_cron = True

        # Errors
        if re.search(r'isError=true', line):
            has_error = True

    if has_error and total_active == 0:
        return "error", "Something went wrong..."

    if total_active == 0:
        return "idle", "Waiting for messages..."

    # Active - determine type
    if has_cron and last_channel in ("", "cron"):
        return "syncing", "Running scheduled tasks..."

    if last_tool:
        return "executing", f"Using {last_tool}..."

    # Map channel to detail
    ch_map = {
        "telegram": "Chatting on Telegram...",
        "slack": "Chatting on Slack...",
        "feishu": "Chatting on Feishu...",
    }
    detail = ch_map.get(last_channel, "Thinking...")
    return "writing", detail


def main():
    print("[bridge] Star-Office-UI bridge starting...", flush=True)
    write_state("idle", "Starting up...")

    prev_state = None
    while True:
        try:
            logs = get_logs()
            if logs:
                state, detail = parse_state(logs)
                key = (state, detail)
                if key != prev_state:
                    write_state(state, detail)
                    prev_state = key
                    print(f"[bridge] → {state}: {detail}", flush=True)
        except Exception as e:
            print(f"[bridge] poll error: {e}", flush=True)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
