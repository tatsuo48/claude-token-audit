#!/usr/bin/env python3
"""UserPromptSubmit hook that asks for confirmation before sending a prompt after the prompt cache has expired."""
import json, os, sys, time
from datetime import datetime

KEEP_DAYS = 90        # state files older than this are cleaned up
STATE_DIR = os.path.join(
    os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "ttl-guard")


def last_main_response(path, tail_bytes=2_000_000):
    """Return (start of the last main-conversation response as epoch, TTL in seconds of the latest cache write).

    One response is written as several lines (one per content block) spread over up to tens of
    seconds, and the cache is refreshed when the request starts, so the earliest line is used.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            lines = f.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return None, None
    last_id, last_ts, ttl = None, None, None
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        msg = e.get("message") or {}
        if msg.get("model") == "<synthetic>":
            continue  # generated locally (errors, interrupts): no API request behind it
        if last_id is None:
            last_id = msg.get("id")
        if msg.get("id") == last_id and e.get("timestamp"):
            last_ts = datetime.fromisoformat(
                e["timestamp"].replace("Z", "+00:00")).timestamp()
        elif ttl is not None:
            break  # past the last response and the TTL is known
        if ttl is None:
            cc = (msg.get("usage") or {}).get("cache_creation") or {}
            if cc.get("ephemeral_1h_input_tokens", 0) > 0:
                ttl = 3600
            elif cc.get("ephemeral_5m_input_tokens", 0) > 0:
                ttl = 300
    return last_ts, ttl


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
    if elapsed < ttl:
        return 0  # cache still alive: sending now is cheap and refreshes it

    # Remember which idle gap was already confirmed; waiting longer does not change the cost.
    ack = os.path.join(STATE_DIR, f"{data.get('session_id', 'unknown')}.ack")
    try:
        with open(ack) as f:
            if f.read() == repr(last_ts):
                return 0  # resend after seeing the warning
    except OSError:
        pass
    with open(ack, "w") as f:
        f.write(repr(last_ts))
    msg = (
        f"⚠️ {fmt(elapsed)} since the last exchange.\n"
        f"The prompt cache expired after {fmt(ttl)}, so sending now will re-send the entire "
        "conversation and cost significantly more.\n\n"
        "  Wrapping up soon          → send the same message again\n"
        "  Still a long way to go    → /compact, then continue\n"
        "  A new session is fine     → /clear\n")
    if ttl == 300:
        msg += (
            "\n💡 Your prompt cache TTL is 5 minutes. If you often step away mid-task,\n"
            '   setting "promptCacheTtl": "1h" in settings.json keeps the cache alive longer\n'
            "   (Claude Code v2.1.242+; cache writes are billed at a higher rate).\n")
    sys.stderr.write(msg)
    return 2


if __name__ == "__main__":
    sys.exit(main())
