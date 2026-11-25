"""The aggregator — turn the event ledger + tracked accounts into a status you can act on.

`status()` is the one call the CLI, the dashboard, and the ASTRO adapter all read: for each
account it computes every limit window (used %, reset countdown, ok/warn/critical/over),
rolls up spend for today/week/month, builds a model mix + peak-hour histogram, and forecasts
where the biggest window lands at its reset. `alerts()` distills that into the "before the
wall" notices at 75/90/100%. All pure over the store — no network.
"""
import time

from . import limits as L


def _spend(events):
    return round(sum((e.get("cost_usd") or 0.0) for e in events), 4)


def _events_for(events, provider):
    return [e for e in events if not provider or e.get("provider") == provider]


def models_breakdown(events, now=None, days=7, top=6):
    """Per-model usage share within the trailing `days` window — provider/model-agnostic, so
    it works for whatever the user plugs in. Returns [{model, requests, tokens, percent}]."""
    now = now if now is not None else time.time()
    start = now - days * L.DAY
    agg = {}
    total = 0
    for e in events:
        if e["ts"] < start:
            continue
        m = e.get("model") or "unknown"
        a = agg.setdefault(m, {"model": m, "requests": 0, "tokens": 0})
        a["requests"] += 1
        a["tokens"] += (e.get("in_tokens") or 0) + (e.get("out_tokens") or 0)
        total += 1
    rows = sorted(agg.values(), key=lambda r: r["requests"], reverse=True)
    if len(rows) > top:
        rest = rows[top:]
        rows = rows[:top] + [{"model": "Other", "requests": sum(r["requests"] for r in rest),
                              "tokens": sum(r["tokens"] for r in rest)}]
    for r in rows:
        r["percent"] = round(r["requests"] / total * 100, 1) if total else 0.0
    return rows


def account_status(acct, events, now=None, caps=None, snaps=None):
    """Windows + spend + primary gauge + per-model breakdown for one account."""
    now = now if now is not None else time.time()
    caps = caps or {}
    snaps = snaps or {}
    evs = _events_for(events, acct["provider"])
    decls = L.windows_for(acct["provider"], acct["plan"])
    wins = []
    for w in decls:
        snap = snaps.get(w["key"])
        if snap and snap.get("used_percent") is not None:
            wins.append(L.from_snapshot(w, snap["used_percent"], snap.get("reset_at")))
        else:
            wev = [e for e in evs if "opus" in (e.get("model") or "").lower()] if w["key"] == "week_opus" else evs
            wins.append(L.measure(w, wev, now=now, cap=caps.get(w["key"])))
    # the gauge = the shortest declared window (the live "session" cap you hit first)
    gauge_key = min(decls, key=lambda w: w["length"])["key"] if decls else None
    gauge = next((w for w in wins if w["key"] == gauge_key), wins[0] if wins else None)
    return {
        "id": acct["id"], "provider": acct["provider"], "plan": acct["plan"],
        "label": acct.get("label") or "", "windows": wins, "gauge": gauge,
        "models_7d": models_breakdown(evs, now=now, days=7),
        "spend_month": _spend([e for e in evs if e["ts"] >= now - 30 * L.DAY]),
    }


def _histogram_hours(events):
    """24-slot local-hour histogram of activity (peak-hours view)."""
    import datetime
    hrs = [0] * 24
    for e in events:
        try:
            hrs[datetime.datetime.fromtimestamp(e["ts"]).hour] += 1
        except Exception:
            pass
    return hrs


def _model_mix(events):
    mix = {}
    for e in events:
        m = e.get("model") or "unknown"
        mix[m] = mix.get(m, 0) + 1
    return dict(sorted(mix.items(), key=lambda kv: kv[1], reverse=True))


def _heatmap(events):
    """7 (Mon..Sun) x 24 (hour) activity counts — the GitHub-style contribution grid."""
    import datetime
    grid = [[0] * 24 for _ in range(7)]
    for e in events:
        try:
            dt = datetime.datetime.fromtimestamp(e["ts"])
            grid[dt.weekday()][dt.hour] += 1
        except Exception:
            pass
    return grid


def _streak_and_level(events, now):
    """A day-streak (consecutive local days with activity, ending today/yesterday) + a simple
    tier from total active days. Original, generic tiers — gamified feel, our own naming."""
    import datetime
    days = {datetime.date.fromtimestamp(e["ts"]).toordinal() for e in events}
    if not days:
        return {"streak": 0, "active_days": 0, "level": 0, "tier": "New"}
    today = datetime.date.fromtimestamp(now).toordinal()
    d = today if today in days else today - 1
    streak = 0
    while d in days:
        streak += 1
        d -= 1
    active = len(days)
    tiers = ["New", "Getting started", "On a roll", "Consistent", "Dialed in",
             "Power user", "Optimizer", "Veteran", "Master", "Legend"]
    level = min(len(tiers) - 1, active // 5)
    return {"streak": streak, "active_days": active, "level": level, "tier": tiers[level]}


def status(store, now=None):
    """Full snapshot across every tracked account. The single source of truth for surfaces."""
    now = now if now is not None else time.time()
    all_events = store.events()
    accts = store.accounts()
    rows = [account_status(a, all_events, now=now, caps=store.caps(a["id"]),
                           snaps=store.snapshots(a["id"])) for a in accts]
    today = [e for e in all_events if e["ts"] >= now - L.DAY]
    week = [e for e in all_events if e["ts"] >= now - L.WEEK]
    month = [e for e in all_events if e["ts"] >= now - 30 * L.DAY]
    # month-end spend forecast: linear from month-to-date burn rate. Floor the elapsed
    # span at 1 day so a brand-new tracker (a few minutes of data) can't extrapolate to
    # an absurd number — worst case it projects one day's spend across the month (~30x).
    span = now - (month[0]["ts"] if month else now)
    elapsed = max(L.DAY, min(30 * L.DAY, span))
    mtd = _spend(month)
    forecast_month = round(mtd / elapsed * (30 * L.DAY), 2) if mtd else 0.0
    ph = _histogram_hours(month)
    peak_hour = ph.index(max(ph)) if any(ph) else None
    return {
        "now": now, "accounts": rows,
        "spend": {"today": _spend(today), "week": _spend(week), "month": mtd,
                  "forecast_month": forecast_month},
        "model_mix": _model_mix(month),
        "models_7d": models_breakdown(all_events, now=now, days=7),
        "peak_hours": ph, "peak_hour": peak_hour,
        "heatmap": _heatmap(month),
        "progress": _streak_and_level(all_events, now),
        "events": len(all_events),
        "unpriced": sorted({e["model"] for e in month
                            if e.get("model") and not (e.get("cost_usd"))}),
    }


def alerts(store, now=None):
    """The 'before the wall' list: any window at/above 75/90/100%. Most-urgent first."""
    st = status(store, now=now)
    out = []
    rank = {"over": 3, "critical": 2, "warn": 1}
    for a in st["accounts"]:
        for w in a["windows"]:
            if w["state"] in rank:
                mins = None
                if w.get("reset_at"):
                    mins = max(0, int((w["reset_at"] - st["now"]) / 60))
                out.append({
                    "account": a["id"], "label": a["label"] or a["id"], "window": w["label"],
                    "used_percent": w["used_percent"], "state": w["state"],
                    "reset_in_min": mins,
                    "message": "%s — %s at %s%% (resets in ~%sm)" % (
                        a["label"] or a["id"], w["label"],
                        w["used_percent"], mins if mins is not None else "?"),
                })
    out.sort(key=lambda x: (rank.get(x["state"], 0), x["used_percent"] or 0), reverse=True)
    return out
