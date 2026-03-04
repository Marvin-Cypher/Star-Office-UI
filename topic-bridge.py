#!/usr/bin/env python3
"""Multi-agent bridge: OpenClaw sessions → Star-Office guest agents.

Discovers thread topics from sessions.json, determines per-topic activity
state from session JSONL files, and syncs each topic as a guest agent.

Channel-agnostic: works with Telegram, Slack, Discord, or any OpenClaw channel.
"""

import json
import os
import glob
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
ROOT_DIR = "/data/star-office"
SESSIONS_DIR = "/data/openclaw/agents/main/sessions"
TOPICS_FILE = os.path.join(ROOT_DIR, "topics.json")
STATE_FILE = os.path.join(ROOT_DIR, "state.json")
AGENTS_FILE = os.path.join(ROOT_DIR, "agents-state.json")

POLL_INTERVAL = 8           # seconds between scans
ACTIVE_WINDOW = 60          # 1 min — topic goes idle quickly after task ends — topic "active" if last entry within this
STALE_WINDOW = 86400 * 7    # 7 days — remove bridge agent if no activity

AREA_MAP = {
    "idle": "breakroom",
    "writing": "writing",
    "researching": "writing",
    "executing": "writing",
    "syncing": "writing",
    "error": "error",
}


# ── Topics config ───────────────────────────────────────────────────────────
def load_topics_config():
    """Load topics config; auto-generate from sessions if file missing."""
    if os.path.exists(TOPICS_FILE):
        try:
            with open(TOPICS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass

    # Auto-generate: discover all threads, assign avatars, show all
    print("[topic-bridge] No topics.json found — auto-discovering topics...", flush=True)
    cfg = auto_discover_topics()
    if cfg:
        save_topics_config(cfg)
        print(f"[topic-bridge] Generated topics.json with {len(cfg)} topics", flush=True)
    return cfg


def save_topics_config(cfg):
    with open(TOPICS_FILE, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ── Thread discovery ────────────────────────────────────────────────────────
def discover_threads():
    """Scan sessions.json for all thread-type sessions."""
    sf = os.path.join(SESSIONS_DIR, "sessions.json")
    if not os.path.exists(sf):
        return {}

    with open(sf, "r") as f:
        sessions = json.load(f)

    threads = {}
    for key, meta in sessions.items():
        if ":thread:" not in key:
            continue
        thread_id = key.split(":thread:")[1]
        threads[thread_id] = meta
    return threads


def build_session_topic_map(threads):
    """Build sessionId → threadId lookup for ALL threads."""
    mapping = {}
    for tid, meta in threads.items():
        sid = meta.get("sessionId", "")
        if sid:
            mapping[sid] = tid
    return mapping


def find_session_file(session_id, thread_id):
    """Find the JSONL file for a thread session."""
    # Try exact pattern first
    pattern = os.path.join(SESSIONS_DIR, f"{session_id}*-topic-{thread_id}.jsonl")
    files = glob.glob(pattern)
    if files:
        return files[0]
    # Fallback: any file with this topic id
    pattern2 = os.path.join(SESSIONS_DIR, f"*-topic-{thread_id}.jsonl")
    files2 = glob.glob(pattern2)
    return files2[0] if files2 else None


# ── Topic name inference ────────────────────────────────────────────────────
def infer_topic_name(session_file):
    """Infer a short topic name from the first few user messages."""
    if not session_file or not os.path.exists(session_file):
        return None

    candidates = []
    try:
        with open(session_file, "r") as f:
            for line in f:
                if len(candidates) >= 5:
                    break
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                msg = entry.get("message", {})
                if msg.get("role") != "user":
                    continue

                text = _extract_user_text(msg)
                if not text or len(text) < 3:
                    continue
                # Skip system/reset messages
                if text.startswith("A new session was started"):
                    continue
                if text.startswith("Pre-compaction memory"):
                    continue
                if text.startswith("Forwarded message context"):
                    continue
                # Skip skill invocations — look for the real message
                if text.startswith('Use the "') and text.endswith("skill for this request."):
                    continue
                candidates.append(text)
    except Exception:
        pass

    if not candidates:
        return None

    # Use first meaningful message, truncated
    first = candidates[0].strip()
    # Clean up common prefixes
    first = re.sub(r"^\[.*?\]\s*", "", first)  # [Audio] prefix etc.
    first = first.split("\n")[0]  # First line only
    if len(first) > 24:
        first = first[:22] + ".."
    return first if first else None


def _extract_user_text(msg):
    """Extract actual user text from a message, stripping metadata wrappers."""
    content = msg.get("content", "")
    if isinstance(content, list):
        for c in content:
            if c.get("type") == "text":
                t = c["text"]
                # Strip Telegram metadata wrapper
                if "Conversation info" in t and "```" in t:
                    parts = t.split("```")
                    if len(parts) >= 3:
                        t = parts[2].strip()
                return t
    elif isinstance(content, str):
        return content
    return ""


# ── Per-topic state detection ───────────────────────────────────────────────
def get_topic_state(session_file):
    """Determine a topic's current state from its latest JSONL entries."""
    if not session_file or not os.path.exists(session_file):
        return "idle", "No activity"

    # Read last ~30KB for recent entries
    last_entries = []
    try:
        fsize = os.path.getsize(session_file)
        with open(session_file, "r") as f:
            if fsize > 30000:
                f.seek(fsize - 30000)
                f.readline()  # skip partial line
            for line in f:
                try:
                    last_entries.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        return "idle", "Read error"

    if not last_entries:
        return "idle", "Empty session"

    last_entries = last_entries[-15:]  # Last 15 entries

    # Check age of most recent entry
    last_ts = None
    for entry in reversed(last_entries):
        ts = entry.get("timestamp")
        if ts:
            try:
                last_ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                pass
            break

    now = datetime.now().astimezone()
    if last_ts:
        age_sec = (now - last_ts).total_seconds()
        if age_sec > ACTIVE_WINDOW:
            return "idle", "Waiting..."

    # Analyze recent entries for state
    for entry in reversed(last_entries):
        msg = entry.get("message", {})
        content = msg.get("content", "")
        role = msg.get("role", "")

        if isinstance(content, list):
            for c in content:
                # Tool call → executing
                if c.get("type") == "toolCall":
                    tool_name = c.get("name", "tool")
                    return "executing", f"Using {tool_name}"
                # Tool error → error
                if c.get("type") == "toolResult" and c.get("isError"):
                    return "error", "Tool error"

        # Assistant response → writing
        if role == "assistant":
            detail = ""
            if isinstance(content, list):
                for c in content:
                    if c.get("type") == "text":
                        detail = c["text"][:40]
                        break
            return "writing", detail or "Composing..."

        # User message → just received input, agent should be working
        if role == "user":
            return "writing", "Processing..."

    return "idle", "Waiting..."


# ── Main agent state (from gateway logs) ────────────────────────────────────
def _read_log_tail(log_path, read_bytes=65536, max_lines=200):
    """Read activity messages from the tail of a single log file."""
    if not os.path.exists(log_path):
        return []
    messages = []
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            read_size = min(size, read_bytes)
            f.seek(size - read_size)
            chunk = f.read().decode("utf-8", errors="replace")

        lines = chunk.split("\n")
        if read_size < size:
            lines = lines[1:]  # skip partial first line

        for raw in lines[-max_lines:]:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            msg = obj.get("1")
            if msg and isinstance(msg, str):
                messages.append(msg)
    except Exception:
        pass
    return messages


def _read_recent_log_messages(max_lines=200):
    """Read recent activity messages from the most recently modified gateway log.

    The gateway log filename is based on gateway start date, NOT current date.
    So we find the most recently modified openclaw-*.log file.
    """
    log_dir = "/tmp/openclaw"
    if not os.path.isdir(log_dir):
        return []

    # Find most recently modified log file
    best_path = None
    best_mtime = 0
    for fname in os.listdir(log_dir):
        if fname.startswith("openclaw-") and fname.endswith(".log"):
            fpath = os.path.join(log_dir, fname)
            try:
                mt = os.path.getmtime(fpath)
                if mt > best_mtime:
                    best_mtime = mt
                    best_path = fpath
            except OSError:
                pass

    if not best_path:
        return []

    return _read_log_tail(best_path, max_lines=max_lines)


def get_main_state(session_topic_map=None):
    """Get main agent state from openclaw gateway logs.

    Reads the raw log file directly (bypasses `openclaw logs` CLI which is
    flooded with config warnings). Parses JSON lines, extracts the "1" field.
    Returns (state, detail, active_topic_name_or_None).
    """
    messages = _read_recent_log_messages()

    if not messages:
        return "idle", "Waiting...", None

    total_active = 0
    last_channel = ""
    last_tool = ""
    last_session_id = ""
    has_error = False
    has_cron = False

    for line in messages:
        m = re.search(r"totalActive=(\d+)", line)
        if m:
            total_active = int(m.group(1))
        # Capture sessionId from "run registered" (has totalActive)
        m = re.search(r"run registered: sessionId=(\S+)", line)
        if m:
            last_session_id = m.group(1)
        # Capture sessionId + channel from "embedded run start" (more specific)
        m = re.search(r"embedded run start.*?sessionId=(\S+).*?messageChannel=(\S+)", line)
        if m:
            last_session_id = m.group(1)
            last_channel = m.group(2)
        m = re.search(r"embedded run tool start.*?tool=(\S+)", line)
        if m:
            last_tool = m.group(1)
        if "cron:" in line and ("run start" in line or "task done" in line):
            has_cron = True
        if re.search(r"isError=true", line):
            has_error = True

    # Resolve active thread ID from sessionId
    active_thread = None
    if session_topic_map and last_session_id:
        active_thread = session_topic_map.get(last_session_id)

    if has_error and total_active == 0:
        detail = "Something went wrong..."
        return "error", detail, active_thread
    if total_active == 0:
        return "idle", "Waiting for messages...", None
    if has_cron and last_channel in ("", "cron"):
        return "syncing", "Running scheduled tasks...", None

    # Active
    if last_tool:
        detail = f"Using {last_tool}..."
        return "executing", detail, active_thread

    ch_map = {
        "telegram": "on Telegram",
        "slack": "on Slack",
        "feishu": "on Feishu",
    }
    ch_label = ch_map.get(last_channel, "")
    if ch_label:
        detail = f"Chatting {ch_label}..."
    else:
        detail = "Thinking..."
    return "writing", detail, active_thread


# ── Agents state management ─────────────────────────────────────────────────
def load_agents():
    if os.path.exists(AGENTS_FILE):
        try:
            with open(AGENTS_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def save_agents(agents):
    with open(AGENTS_FILE, "w") as f:
        json.dump(agents, f, ensure_ascii=False, indent=2)


def sync_agents(main_state, main_detail, topic_agents):
    """Sync the agents state file with current main + topic agents.

    Preserves externally-joined agents (source != "bridge").
    """
    agents = load_agents()

    # Separate: main agent, bridge agents, external agents
    main_agent = None
    bridge_agents = {}
    external_agents = []

    for a in agents:
        if a.get("isMain"):
            main_agent = a
        elif a.get("source") == "bridge":
            bridge_agents[a.get("agentId", "")] = a
        else:
            external_agents.append(a)

    # Update or create main agent
    now_iso = datetime.now().isoformat()
    if not main_agent:
        main_agent = {
            "agentId": "clawdi",
            "name": "Clawdi",
            "isMain": True,
            "source": "local",
            "joinKey": None,
            "authStatus": "approved",
            "authExpiresAt": None,
            "lastPushAt": None,
        }
    main_agent["state"] = main_state
    main_agent["detail"] = main_detail
    main_agent["area"] = AREA_MAP.get(main_state, "breakroom")
    main_agent["updated_at"] = now_iso

    # Build new bridge agents list
    new_bridge = {}
    for ta in topic_agents:
        aid = ta["agentId"]
        existing = bridge_agents.get(aid)
        if existing:
            # Update existing
            existing["state"] = ta["state"]
            existing["detail"] = ta["detail"]
            existing["area"] = AREA_MAP.get(ta["state"], "breakroom")
            existing["updated_at"] = now_iso
            existing["lastPushAt"] = now_iso
            if ta.get("name"):
                existing["name"] = ta["name"]
            if ta.get("avatar") is not None:
                existing["avatar"] = ta["avatar"]
            new_bridge[aid] = existing
        else:
            # New bridge agent
            new_bridge[aid] = {
                "agentId": aid,
                "name": ta["name"],
                "isMain": False,
                "state": ta["state"],
                "detail": ta["detail"],
                "area": AREA_MAP.get(ta["state"], "breakroom"),
                "source": "bridge",
                "joinKey": None,
                "authStatus": "approved",
                "authExpiresAt": None,
                "lastPushAt": now_iso,
                "updated_at": now_iso,
                "threadId": ta.get("threadId"),
                "channel": ta.get("channel"),
                "avatar": ta.get("avatar"),
            }

    # Assemble final list: main + bridge + external
    result = [main_agent] + list(new_bridge.values()) + external_agents
    save_agents(result)
    return len(new_bridge)


# ── Write main state.json (for backward compat) ────────────────────────────
def write_main_state(state, detail):
    data = {
        "state": state,
        "detail": detail[:200] if detail else "",
        "progress": 0,
        "updated_at": datetime.now().isoformat(),
    }
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Main loop ───────────────────────────────────────────────────────────────
def main():
    print("[topic-bridge] Starting multi-agent bridge...", flush=True)

    # Auto-generate topics.json if missing
    topics_cfg = load_topics_config()
    if not topics_cfg:
        print("[topic-bridge] Generating initial topics.json...", flush=True)
        topics_cfg = auto_discover_topics()
        save_topics_config(topics_cfg)
        print(f"[topic-bridge] Created topics.json with {len(topics_cfg)} topics", flush=True)

    prev_main = None
    cycle = 0

    while True:
        try:
            # Reload topics config periodically (user may edit)
            if cycle % 10 == 0:
                topics_cfg = load_topics_config()

            # 1. Build session→thread lookup (all threads, not just topics.json)
            threads = discover_threads()
            session_topic_map = build_session_topic_map(threads)

            # 2. Main agent state from gateway logs (active_thread = threadId)
            main_state, main_detail, active_thread = get_main_state(session_topic_map)
            main_key = (main_state, main_detail, active_thread)
            if main_key != prev_main:
                write_main_state(main_state, main_detail)
                prev_main = main_key
                thread_label = f" [thread:{active_thread}]" if active_thread else ""
                print(f"[topic-bridge] Main → {main_state}: {main_detail}{thread_label}", flush=True)

            # 2b. Fallback: if main is active but active_thread is not a
            #     topic in our config, find the most recently updated topic
            #     thread from sessions.json as a best-guess.
            if main_state != "idle" and (not active_thread or active_thread not in topics_cfg):
                best_tid = None
                best_ts = 0
                for tid_candidate, meta_candidate in threads.items():
                    if tid_candidate not in topics_cfg:
                        continue
                    ts = meta_candidate.get("updatedAt", 0)
                    if ts > best_ts:
                        best_ts = ts
                        best_tid = tid_candidate
                if best_tid:
                    active_thread = best_tid

            # 3. Build topic agent list
            topic_agents = []
            now = datetime.now().astimezone()

            for tid, meta in threads.items():
                sid = meta.get("sessionId", "")
                channel = meta.get("deliveryContext", {}).get("channel", "unknown")
                updated_ms = meta.get("updatedAt", 0)

                # Only sync topics that are in topics.json with show=true
                topic_info = topics_cfg.get(tid, {})
                if isinstance(topic_info, dict):
                    if not topic_info.get("show", False):
                        continue
                    name = topic_info.get("name")
                else:
                    continue  # Skip topics not in config

                # Skip very stale topics
                if updated_ms:
                    age = (time.time() * 1000 - updated_ms) / 1000
                    if age > STALE_WINDOW:
                        continue

                if not name:
                    sf = find_session_file(sid, tid)
                    name = infer_topic_name(sf) or f"Topic {tid}"

                # Get topic state from session file
                sf = find_session_file(sid, tid)
                state, detail = get_topic_state(sf)

                # Override: if main agent is active on THIS thread, mirror its state
                if active_thread and active_thread == tid and main_state != "idle":
                    state = main_state
                    detail = main_detail
                    print(f"[topic-bridge] Topic {name} (thread {tid}) → {state} (from main)", flush=True)

                avatar_idx = topic_info.get("avatar") if isinstance(topic_info, dict) else None
                topic_agents.append({
                    "agentId": f"topic_{tid}",
                    "name": name,
                    "state": state,
                    "detail": detail,
                    "threadId": tid,
                    "channel": channel,
                    "avatar": avatar_idx,
                })

            # 4. Sync to agents state
            count = sync_agents(main_state, main_detail, topic_agents)

            if cycle % 30 == 0:  # Log every ~4 min
                active = sum(1 for a in topic_agents if a["state"] != "idle")
                print(f"[topic-bridge] {count} topics ({active} active)", flush=True)

        except Exception as e:
            print(f"[topic-bridge] Error: {e}", flush=True)

        cycle += 1
        time.sleep(POLL_INTERVAL)


def auto_discover_topics():
    """Auto-generate topics config from session data with avatars."""
    threads = discover_threads()
    cfg = {}
    avatar = 1
    for tid, meta in sorted(threads.items(), key=lambda x: x[1].get("updatedAt", 0), reverse=True):
        sid = meta.get("sessionId", "")
        sf = find_session_file(sid, tid)
        name = infer_topic_name(sf)
        channel = meta.get("deliveryContext", {}).get("channel", "unknown")
        cfg[tid] = {
            "name": name or f"Topic {tid}",
            "channel": channel,
            "show": True,
            "avatar": avatar,
        }
        avatar = (avatar % 10) + 1  # cycle 1-10
    return cfg


def merge_new_threads(topics_cfg):
    """Check for new threads not yet in topics.json and add them."""
    threads = discover_threads()
    added = 0
    # Find highest avatar in existing config to continue cycling
    max_avatar = 0
    for info in topics_cfg.values():
        if isinstance(info, dict):
            a = info.get("avatar", 0)
            if a > max_avatar:
                max_avatar = a
    avatar = (max_avatar % 10) + 1

    for tid, meta in threads.items():
        if tid in topics_cfg:
            continue
        sid = meta.get("sessionId", "")
        sf = find_session_file(sid, tid)
        name = infer_topic_name(sf)
        channel = meta.get("deliveryContext", {}).get("channel", "unknown")
        topics_cfg[tid] = {
            "name": name or f"Topic {tid}",
            "channel": channel,
            "show": True,
            "avatar": avatar,
        }
        avatar = (avatar % 10) + 1
        added += 1
    if added:
        save_topics_config(topics_cfg)
        print(f"[topic-bridge] Auto-added {added} new topic(s) to topics.json", flush=True)
    return topics_cfg


if __name__ == "__main__":
    main()
