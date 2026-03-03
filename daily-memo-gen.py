#!/usr/bin/env python3
"""Generate daily memory memo from OpenClaw session logs.

Scans session JSONL files for a target date and writes a summary to
/data/openclaw/workspace/memory/YYYY-MM-DD.md

Designed to run as a daily cron at 23:55 UTC.
"""
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta

SESSIONS_DIR = "/data/openclaw/agents/main/sessions"
MEMORY_DIR = "/data/openclaw/workspace/memory"

def get_target_date():
    now = datetime.now()
    if now.hour < 4:
        target = now - timedelta(days=2)
    else:
        target = now - timedelta(days=1)
    return target.strftime("%Y-%m-%d")

def extract_telegram_text(text):
    """Extract actual user message from Telegram metadata wrapper."""
    if "Conversation info" not in text:
        return None
    parts = text.split("```")
    if len(parts) >= 3:
        actual = parts[2].strip()
        if actual and len(actual) > 3:
            return actual.split('\n')[0][:150]
    return None

def scan_session_file(filepath, target_date):
    telegram_msgs = []
    tools_used = Counter()
    cron_jobs = []
    models_used = set()
    message_count = 0

    try:
        with open(filepath, 'r') as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except:
                    continue
                ts = entry.get("timestamp", "")
                if target_date not in ts:
                    continue
                if entry.get("type") != "message":
                    continue

                msg = entry.get("message", {})
                role = msg.get("role", "")
                content = msg.get("content", "")

                if role == "user" and content:
                    message_count += 1
                    text = ""
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                text = c.get("text", "")
                                break
                    elif isinstance(content, str):
                        text = content
                    if not text:
                        continue

                    # Cron jobs
                    cron_match = re.match(r'\[cron:\S+ (\S+)\]', text)
                    if cron_match:
                        cron_jobs.append(cron_match.group(1))
                        continue

                    # Telegram user messages
                    tg_text = extract_telegram_text(text)
                    if tg_text:
                        # Skip callback button presses (xe*A_, xe*S_, xe*E_)
                        if re.match(r'^xe\w{2,4}[ASE]_\d+$', tg_text):
                            continue
                        telegram_msgs.append(tg_text)

                elif role == "assistant" and isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "toolCall":
                            tools_used[c.get("name", "?")] += 1
                    model = msg.get("model", "")
                    if model:
                        models_used.add(model.split("/")[-1])
    except:
        return None

    if message_count == 0:
        return None
    return {
        "telegram_msgs": telegram_msgs,
        "tools_used": tools_used,
        "cron_jobs": cron_jobs,
        "models_used": models_used,
        "message_count": message_count,
    }

def generate_memo(target_date):
    all_tg, all_tools, all_crons = [], Counter(), []
    total_messages, sessions_active = 0, 0

    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    day_start = target_dt.timestamp()

    for fname in os.listdir(SESSIONS_DIR):
        if not fname.endswith('.jsonl'):
            continue
        fpath = os.path.join(SESSIONS_DIR, fname)
        try:
            if os.path.getmtime(fpath) < day_start - 86400:
                continue
        except:
            continue

        result = scan_session_file(fpath, target_date)
        if result:
            sessions_active += 1
            all_tg.extend(result["telegram_msgs"])
            all_tools.update(result["tools_used"])
            all_crons.extend(result["cron_jobs"])
            total_messages += result["message_count"]

    lines = [f"# {target_date}\n"]

    if total_messages > 0:
        lines.append(f"**{total_messages} messages across {sessions_active} sessions**\n")

    # Conversations (actual Telegram user messages)
    if all_tg:
        lines.append("## Conversations")
        seen = set()
        count = 0
        for msg in all_tg:
            key = msg[:30].lower()
            if key not in seen and count < 10:
                seen.add(key)
                msg = re.sub(r'[^\x20-\x7e]', '', msg).strip()
                if msg and len(msg) > 5:
                    lines.append(f"- {msg}")
                    count += 1
        lines.append("")

    # Cron jobs
    if all_crons:
        cron_counts = Counter(all_crons)
        lines.append("## Cron Jobs")
        for job, count in cron_counts.most_common():
            lines.append(f"- {job} ({count} runs)")
        lines.append("")

    # Tools
    if all_tools:
        top = all_tools.most_common(6)
        lines.append("## Tools")
        lines.append(f"- {', '.join(f'{n} ({c})' for n, c in top)}")
        lines.append("")

    if total_messages == 0:
        lines.append("- No significant activity recorded.\n")

    return "\n".join(lines)

def main():
    target_date = get_target_date()
    memo_path = os.path.join(MEMORY_DIR, f"{target_date}.md")

    if os.path.exists(memo_path):
        size = os.path.getsize(memo_path)
        if size > 200:
            print(f"Memo exists for {target_date} ({size} bytes), skipping")
            return

    os.makedirs(MEMORY_DIR, exist_ok=True)
    memo = generate_memo(target_date)
    with open(memo_path, 'w') as f:
        f.write(memo)
    print(f"Wrote {memo_path} ({len(memo)} bytes)")

if __name__ == "__main__":
    main()
