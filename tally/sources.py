"""Local-first source readers — collect usage your AI tools already wrote to disk.

The whole point (and the thing SaaS trackers charge for): your tools already log token
usage locally. Read it, normalize it, store it — no API keys, no cloud. Each reader yields
normalized event dicts for `store.add_event`; `dedup` keys make re-imports idempotent so a
watch loop can re-scan the same files safely.

Bundled readers:
  · claude_code — ~/.claude/projects/**/*.jsonl assistant-message token usage.
  · codex       — ~/.codex/sessions/**/*.jsonl token counts, plus the latest rate-limit read.
  · jsonl       — a generic {ts,model,in_tokens,out_tokens,...} line file you point at.

More tools (Codex rollouts, Cursor sqlite, etc.) plug in as new reader functions — the
event shape is the only contract.
"""
import glob
import json
import os

from . import pricing


def _home():
    return os.environ.get("TALLY_FAKE_HOME") or os.path.expanduser("~")


def _iso_to_epoch(s):
    """Best-effort ISO-8601 -> epoch seconds (stdlib only)."""
    if not s:
        return None
    try:
        import datetime
        s = s.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def claude_code(home=None):
    """Read Claude Code session logs (~/.claude/projects/**/*.jsonl). Yields one event per
    assistant message that carries a usage block. Cost is computed from the price table."""
    home = home or _home()
    root = os.path.join(home, ".claude", "projects")
    if not os.path.isdir(root):
        return
    for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f):
                    line = line.strip()
                    if not line or '"usage"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    msg = rec.get("message") or rec
                    usage = msg.get("usage") if isinstance(msg, dict) else None
                    if not isinstance(usage, dict):
                        continue
                    model = (msg.get("model") if isinstance(msg, dict) else None) or rec.get("model") or ""
                    it = usage.get("input_tokens") or 0
                    ot = usage.get("output_tokens") or 0
                    cr = usage.get("cache_read_input_tokens") or 0
                    cw = usage.get("cache_creation_input_tokens") or 0
                    if not (it or ot or cr or cw):
                        continue
                    ts = _iso_to_epoch(rec.get("timestamp")) or (os.path.getmtime(path))
                    mid = (msg.get("id") if isinstance(msg, dict) else None) or ("%s#%d" % (os.path.basename(path), i))
                    yield {
                        "ts": ts, "source": "claude_code", "provider": "claude", "model": model,
                        "kind": "api", "in_tokens": it, "out_tokens": ot, "cache_read": cr,
                        "cache_write": cw, "reasoning": 0,
                        "cost_usd": pricing.cost(model, it, ot, cr, cw),
                        "dedup": "claude_code:%s" % mid,
                    }
        except Exception:
            continue


def _codex_files(home):
    root = os.path.join(home, ".codex", "sessions")
    return sorted(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True))


def codex(home=None):
    """Read Codex session files (~/.codex/sessions/**/*.jsonl). One event per `token_count`
    record, using that turn's own usage (`last_token_usage`). The model comes from the most
    recent `turn_context`. Codex counts cached tokens inside input, so they are split out here."""
    home = home or _home()
    for path in _codex_files(home):
        model = ""
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f):
                    if '"turn_context"' in line:
                        try:
                            model = (json.loads(line).get("payload") or {}).get("model") or model
                        except Exception:
                            pass
                        continue
                    if '"token_count"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    info = (rec.get("payload") or {}).get("info") or {}
                    u = info.get("last_token_usage")
                    if not isinstance(u, dict):
                        continue
                    cached = u.get("cached_input_tokens") or 0
                    it = max(0, (u.get("input_tokens") or 0) - cached)
                    ot = u.get("output_tokens") or 0
                    if not (it or ot or cached):
                        continue
                    ts = _iso_to_epoch(rec.get("timestamp")) or os.path.getmtime(path)
                    yield {
                        "ts": ts, "source": "codex", "provider": "codex", "model": model,
                        "kind": "subscription", "in_tokens": it, "out_tokens": ot,
                        "cache_read": cached, "cache_write": u.get("cache_write_input_tokens") or 0,
                        "reasoning": u.get("reasoning_output_tokens") or 0,
                        "cost_usd": pricing.cost(model, it, ot, cached, 0, reasoning_in_output=True),
                        "dedup": "codex:%s#%d" % (os.path.basename(path), i),
                    }
        except Exception:
            continue


def codex_limits(home=None):
    """Latest Codex rate-limit read found on disk: {plan_type, windows:[{key, used_percent,
    window_minutes, reset_at}]} or None. Codex reports used % and a reset time directly."""
    home = home or _home()
    best_ts, best = -1, None
    for path in _codex_files(home):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                last = None
                for line in f:
                    if '"rate_limits"' in line and '"token_count"' in line:
                        last = line
                if last:
                    rec = json.loads(last)
                    ts = _iso_to_epoch(rec.get("timestamp")) or 0
                    if ts > best_ts:
                        best_ts, best = ts, (rec.get("payload") or {}).get("rate_limits")
        except Exception:
            continue
    if not isinstance(best, dict):
        return None
    wins = []
    for slot, default in (("primary", "week"), ("secondary", "secondary")):
        w = best.get(slot)
        if isinstance(w, dict) and w.get("used_percent") is not None:
            mins = w.get("window_minutes")
            key = "5h" if mins == 300 else ("week" if mins == 10080 else default)
            wins.append({"key": key, "used_percent": w["used_percent"],
                         "window_minutes": mins, "reset_at": w.get("resets_at")})
    return {"plan_type": best.get("plan_type"), "windows": wins} if wins else None


def jsonl(path):
    """Import a generic JSONL usage file: each line {ts?,model,in_tokens,out_tokens,cache_read?,
    cache_write?,provider?,cost_usd?}. Missing cost is computed; missing ts -> now at import."""
    if not path or not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            model = r.get("model") or ""
            it, ot = r.get("in_tokens") or 0, r.get("out_tokens") or 0
            cr, cw = r.get("cache_read") or 0, r.get("cache_write") or 0
            cost = r.get("cost_usd")
            if cost is None:
                cost = pricing.cost(model, it, ot, cr, cw)
            yield {
                "ts": r.get("ts"), "source": r.get("source") or "jsonl",
                "provider": r.get("provider") or "", "model": model, "kind": r.get("kind") or "api",
                "in_tokens": it, "out_tokens": ot, "cache_read": cr, "cache_write": cw,
                "reasoning": r.get("reasoning") or 0, "cost_usd": cost,
                "dedup": r.get("dedup") or ("jsonl:%s:%d" % (os.path.basename(path), i)),
            }


READERS = {"claude_code": claude_code, "codex": codex}


def ingest(store, readers=None, home=None):
    """Run readers, add every event to `store` (idempotent via dedup). Returns count added."""
    n = 0
    for name in (readers or list(READERS)):
        fn = READERS.get(name)
        if not fn:
            continue
        for ev in fn(home=home):
            if store.add_event(**ev) is not None:
                n += 1
    # Codex tells us its own used % — keep it as a snapshot for any tracked codex account.
    lim = codex_limits(home)
    if lim:
        for acct in store.accounts():
            if acct["provider"] == "codex":
                for w in lim["windows"]:
                    store.set_snapshot(acct["id"], w["key"], w["used_percent"], w["reset_at"])
    return n
