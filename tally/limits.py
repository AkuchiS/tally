"""Provider / plan limit registry + window math.

A **window** is the unit every AI limit reduces to (the pattern every usage tracker
converges on): a rolling or fixed period with a cap, plus a reset time. Two ways a window
gets its "used" value:

  · COMPUTED  — we count our own recorded events inside the period against `cap`
                (works for anything we can read locally: tokens, requests, or $).
  · SNAPSHOT  — the provider tells us used_percent + reset_at directly (e.g. Claude's
                OAuth usage endpoint); we just store and display it.

The PLANS registry declares the windows each subscription exposes (Claude Max's 5-hour
session + weekly caps, ChatGPT/Cursor/Perplexity request caps, etc.). Caps that vary or
aren't public are left None — the window still tracks *usage*, just without a % bar until
you set a cap (`tally cap ...`) or a snapshot arrives.
"""
import time

HOUR = 3600
DAY = 24 * HOUR
WEEK = 7 * DAY

# provider -> plan -> [windows]. cap is in `unit` (tokens|requests|usd); None = unknown.
# kind: "session" = fixed rolling window from first use; "rolling" = trailing period.
PLANS = {
    "claude": {
        "pro":  [
            {"key": "5h",  "label": "5-hour session", "kind": "session", "length": 5 * HOUR, "unit": "requests", "cap": None},
            {"key": "week", "label": "Weekly",        "kind": "rolling", "length": WEEK,     "unit": "requests", "cap": None},
        ],
        "max5": [
            {"key": "5h",  "label": "5-hour session", "kind": "session", "length": 5 * HOUR, "unit": "requests", "cap": None},
            {"key": "week", "label": "Weekly",        "kind": "rolling", "length": WEEK,     "unit": "requests", "cap": None},
            {"key": "week_opus", "label": "Weekly (Opus)", "kind": "rolling", "length": WEEK, "unit": "requests", "cap": None},
        ],
        "max20":[
            {"key": "5h",  "label": "5-hour session", "kind": "session", "length": 5 * HOUR, "unit": "requests", "cap": None},
            {"key": "week", "label": "Weekly",        "kind": "rolling", "length": WEEK,     "unit": "requests", "cap": None},
            {"key": "week_opus", "label": "Weekly (Opus)", "kind": "rolling", "length": WEEK, "unit": "requests", "cap": None},
        ],
    },
    # Codex reports used % + reset itself (snapshot); the weekly window is the one it shows.
    "codex": {
        "plus": [{"key": "week", "label": "Weekly", "kind": "rolling", "length": WEEK, "unit": "requests", "cap": None}],
        "pro":  [{"key": "week", "label": "Weekly", "kind": "rolling", "length": WEEK, "unit": "requests", "cap": None}],
    },
    "chatgpt": {
        "plus": [{"key": "3h", "label": "3-hour cap", "kind": "session", "length": 3 * HOUR, "unit": "requests", "cap": None}],
        "pro":  [{"key": "3h", "label": "3-hour cap", "kind": "session", "length": 3 * HOUR, "unit": "requests", "cap": None}],
    },
    "cursor": {
        "pro": [{"key": "month", "label": "Monthly requests", "kind": "rolling", "length": 30 * DAY, "unit": "requests", "cap": 500}],
    },
    "perplexity": {
        "pro": [{"key": "day", "label": "Daily Pro searches", "kind": "rolling", "length": DAY, "unit": "requests", "cap": 300}],
    },
    "grok": {
        "sup": [{"key": "2h", "label": "2-hour cap", "kind": "session", "length": 2 * HOUR, "unit": "requests", "cap": None}],
    },
    "gemini": {
        "pro": [{"key": "day", "label": "Daily cap", "kind": "rolling", "length": DAY, "unit": "requests", "cap": None}],
    },
    # generic "api" account: no subscription window, just a monthly $ budget you set.
    "api": {
        "default": [{"key": "month", "label": "Monthly spend", "kind": "rolling", "length": 30 * DAY, "unit": "usd", "cap": None}],
    },
}

# state thresholds (percent) — the "before the wall" alert ladder.
WARN, HIGH, FULL = 75.0, 90.0, 100.0


def windows_for(provider, plan):
    """Declared windows for a provider/plan (empty list if unknown — still trackable)."""
    return [dict(w) for w in PLANS.get((provider or "").lower(), {}).get((plan or "").lower(), [])]


def state_of(used_percent):
    """Map a used-% onto the alert ladder."""
    if used_percent is None:
        return "unknown"
    if used_percent >= FULL:
        return "over"
    if used_percent >= HIGH:
        return "critical"
    if used_percent >= WARN:
        return "warn"
    return "ok"


def window_start(win, now):
    """Epoch at which the current period began (for a trailing/rolling or session window)."""
    return now - win["length"]


def measure(win, events, now=None, cap=None, unit_of=None):
    """Compute a window's status from recorded events.

    events: iterable of dicts with at least {ts, <unit fields>}. `unit_of(ev)` returns the
    amount this event contributes in the window's unit (requests->1, tokens->in+out, usd->cost).
    cap overrides the registry cap. Returns a normalized window dict.
    """
    now = now if now is not None else time.time()
    cap = cap if cap is not None else win.get("cap")
    unit = win["unit"]
    if unit_of is None:
        def unit_of(ev):
            if unit == "requests":
                return 1
            if unit == "tokens":
                return (ev.get("in_tokens") or 0) + (ev.get("out_tokens") or 0)
            if unit == "usd":
                return ev.get("cost_usd") or 0.0
            return 0
    start = window_start(win, now)
    used = 0
    oldest_in = None
    for ev in events:
        ts = ev.get("ts", 0)
        if ts >= start:
            used += unit_of(ev)
            if oldest_in is None or ts < oldest_in:
                oldest_in = ts
    # reset_at: a rolling window frees up when its oldest in-window event ages out;
    # a session window resets `length` after it began (oldest event ~ session start).
    if oldest_in is not None:
        reset_at = oldest_in + win["length"]
    else:
        reset_at = now + win["length"]
    used_percent = round(used / cap * 100, 1) if cap else None
    return {
        "key": win["key"], "label": win["label"], "unit": unit,
        "used": round(used, 4) if unit == "usd" else used,
        "cap": cap, "used_percent": used_percent, "reset_at": reset_at,
        "remaining": (round(cap - used, 4) if cap is not None else None),
        "state": state_of(used_percent),
    }


def from_snapshot(win, used_percent, reset_at):
    """Build a window straight from a provider-supplied snapshot (used% + reset)."""
    return {
        "key": win["key"], "label": win["label"], "unit": win.get("unit", "requests"),
        "used": None, "cap": None, "used_percent": used_percent, "reset_at": reset_at,
        "remaining": None, "state": state_of(used_percent),
    }
