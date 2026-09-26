#!/usr/bin/env python3
"""UserPromptSubmit hook that warns before sending a prompt after the prompt cache has (nearly) expired."""
import json, os, sys, time
from datetime import datetime

MARGIN = 0.9          # warn once this fraction of the TTL has elapsed
ACK_WINDOW = 10 * 60  # a resend within this many seconds after a warning goes through
KEEP_DAYS = 90        # state files older than this are cleaned up
STATE_DIR = os.path.join(
    os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "ttl-guard")


def last_main_response(path, tail_bytes=2_000_000):
    """Return (last main-conversation response time as epoch, TTL in seconds of the latest cache write)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            lines = f.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return None, None
    last_ts = None
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        if (e.get("message") or {}).get("model") == "<synthetic>":
            continue  # generated locally (errors, interrupts): no API request behind it
        if last_ts is None and e.get("timestamp"):
            last_ts = datetime.fromisoformat(
                e["timestamp"].replace("Z", "+00:00")).timestamp()
        cc = ((e.get("message") or {}).get("usage") or {}).get("cache_creation") or {}
        if cc.get("ephemeral_1h_input_tokens", 0) > 0:
            return last_ts, 3600
        if cc.get("ephemeral_5m_input_tokens", 0) > 0:
            return last_ts, 300
    return last_ts, None


def cleanup(now):
    try:
        for ent in os.scandir(STATE_DIR):
            if now - ent.stat().st_mtime > KEEP_DAYS * 86400:
                os.remove(ent.path)
    except OSError:
        pass


def fmt(sec):
    if sec >= 60:
        m = int(sec // 60)
        return f"{m} minute" + ("s" if m != 1 else "")
    s = int(sec)
    return f"{s} second" + ("s" if s != 1 else "")


def main():
    data = json.load(sys.stdin)
    if (data.get("prompt") or "").lstrip().startswith("/"):
        return 0  # never block slash commands such as /clear or /compact
    now = time.time()
    os.makedirs(STATE_DIR, exist_ok=True)
    cleanup(now)

    last_ts, ttl = last_main_response(data.get("transcript_path") or "")
    if last_ts is None or ttl is None:
        return 0  # first turn, or prompt caching disabled: nothing to check
    elapsed = now - last_ts
    ack = os.path.join(STATE_DIR, f"{data.get('session_id', 'unknown')}.ack")

    if elapsed < ttl * MARGIN:
        if os.path.exists(ack):
            os.remove(ack)
        return 0
    if os.path.exists(ack) and now - os.path.getmtime(ack) < ACK_WINDOW:
        os.remove(ack)
        return 0  # resend after seeing the warning

    open(ack, "w").close()
    msg = (
        f"⚠️ {fmt(elapsed)} since the last exchange.\n"
        f"The prompt cache expires after {fmt(ttl)}, so sending now will re-send the entire "
        "conversation and cost significantly more.\n\n"
        "  Recommended           → /clear to start fresh\n"
        "  Still mid-task        → /compact [what to keep] to summarize, then continue\n"
        "  Don't mind the cost   → send the same message again\n")
    if ttl == 300:
        msg += (
            "\n💡 Your prompt cache TTL is 5 minutes. If you often step away mid-task,\n"
            '   setting "promptCacheTtl": "1h" in settings.json keeps the cache alive longer\n'
            "   (Claude Code v2.1.242+; cache writes are billed at a higher rate).\n")
    sys.stderr.write(msg)
    return 2


if __name__ == "__main__":
    sys.exit(main())
