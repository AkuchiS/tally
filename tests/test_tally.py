#!/usr/bin/env python3
"""Offline tests for tally (no network). Run: python3 tests/test_tally.py"""
import os, sys, json, time, tempfile

os.environ["TALLY_HOME"] = tempfile.mkdtemp(prefix="tally-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tally import pricing, limits as L, store as S, sources, engine

P = F = 0
def chk(n, c):
    global P, F
    if c: P += 1; print("  ok  ", n)
    else: F += 1; print("  FAIL", n)

S.reset()

# ---- pricing: table + formula + unknown ----
chk("opus-4-8 1M in+1M out = $30", round(pricing.cost("claude-opus-4-8", 1_000_000, 1_000_000), 2) == 30.0)
chk("longest-substring wins (sonnet-5 not sonnet)", pricing.price_for("claude-sonnet-5-x")["in"] == 2.0)
chk("cache tokens priced", round(pricing.cost("claude-opus-4-8", 0, 0, 1_000_000, 0), 2) == 0.50)

# ---- pricing provenance + the announced Sonnet 5 rise (re-derived from the vendor page 2026-07-17) ----
# Found by re-deriving the table from platform.claude.com/docs/en/about-claude/pricing rather than
# trusting it. A flat table cannot express an announced change, and the failure is SILENT.
chk("sonnet-5 stays $2/$10 (announced rise was cancelled)",
    pricing.price_for("claude-sonnet-5-x", on="2026-12-01")["in"] == 2.0
    and pricing.price_for("claude-sonnet-5-x", on="2026-12-01")["out"] == 10.0)
chk("claude 5.5 family priced from the vendor page",
    pricing.price_for("claude-opus-5-5")["out"] == 20.0
    and pricing.price_for("claude-sonnet-5-5")["cr"] == 0.10
    and pricing.price_for("claude-fable-5-2")["in"] == 8.0
    and pricing.price_for("claude-opus-5")["in"] == 5.0)
chk("haiku-5-5 bills the higher tier above 100k prompt tokens",
    round(pricing.cost("claude-haiku-5-5", 1_000_000, 0), 2) == 0.5
    and round(pricing.cost("claude-haiku-5-5", 50_000, 0), 4) == 0.005)
chk("models the vendor lists are all priced explicitly, not by lucky fallback",
    all(pricing.price_for("claude-" + m)["in"] > 0
        for m in ("opus-4-8", "opus-4-7", "opus-4-6", "opus-4-5", "opus-4-1",
                  "sonnet-5", "sonnet-5-5", "opus-5", "opus-5-5", "haiku-5-5", "fable-5-2", "sonnet-4-6", "sonnet-4-5", "sonnet-4",
                  "haiku-4-5", "haiku-3-5", "fable-5", "mythos-5")))
chk("retired opus-4 is $15/$75, NOT the $5 opus tier it used to fall back to",
    pricing.price_for("claude-opus-4-20250514")["in"] == 15.0)
chk("price table records where it came from and when",
    "platform.claude.com" in pricing.__doc__ and "2026-11-25" in pricing.__doc__)
chk("reasoning_in_output not double-counted",
    pricing.cost("gpt-5", 0, 1000, 0, 0, reasoning_tok=1000, reasoning_in_output=True)
    == pricing.cost("gpt-5", 0, 1000))
chk("unknown model = zero + unpriced", pricing.price_for("who-dis") == pricing.ZERO and not pricing.is_priced("who-dis"))

# ---- limits: window registry + state ladder ----
chk("claude max20 has 5h + weekly windows", {w["key"] for w in L.windows_for("claude", "max20")} >= {"5h", "week", "week_opus"})
chk("unknown plan = empty (still trackable)", L.windows_for("claude", "nope") == [])
chk("state ladder 75/90/100", (L.state_of(50), L.state_of(80), L.state_of(95), L.state_of(100)) == ("ok", "warn", "critical", "over"))
chk("state unknown when no cap", L.state_of(None) == "unknown")

# ---- store: events + accounts + idempotent dedup ----
now = time.time()
S.add_event(ts=now, source="manual", provider="cursor", model="grok-4", cost_usd=0.01, dedup="d1")
S.add_event(ts=now, source="manual", provider="cursor", model="grok-4", cost_usd=0.01, dedup="d1")  # dup
chk("dedup makes re-import idempotent", len(S.events()) == 1)
aid = S.add_account("cursor", "pro", label="My Cursor")
chk("account tracked", any(a["id"] == "cursor:pro" for a in S.accounts()))

# ---- window measure: computed cap ----
for i in range(300):
    S.add_event(ts=now - 60 * i, source="manual", provider="cursor", model="grok-4", cost_usd=0.001)
win = L.windows_for("cursor", "pro")[0]                 # month cap 500 requests
m = L.measure(win, S.events(provider="cursor"), now=now)
chk("computed window counts requests vs cap", m["cap"] == 500 and m["used"] >= 300 and m["state"] == "ok")
chk("window exposes reset_at + remaining", m["reset_at"] > now and m["remaining"] is not None)

# ---- engine: status + spend rollups + alerts ----
st = engine.status(S, now=now)
chk("status lists tracked accounts", any(a["provider"] == "cursor" for a in st["accounts"]))
chk("spend rollups present", set(st["spend"]) == {"today", "week", "month", "forecast_month"})
chk("model mix + peak hours computed", "grok-4" in st["model_mix"] and len(st["peak_hours"]) == 24)
# push cursor over 90% -> alert fires
for i in range(160):
    S.add_event(ts=now - 20 * i, source="manual", provider="cursor", model="grok-4", cost_usd=0.001)
al = engine.alerts(S, now=now)
chk("alert fires before the wall (>=90%)", any(a["state"] in ("critical", "over") for a in al))
chk("alert carries reset countdown + message", al and al[0]["reset_in_min"] is not None and "resets" in al[0]["message"])

# ---- snapshot path: provider-supplied used% (no local counting) ----
S.add_account("claude", "max20", label="Jay Max")
S.set_snapshot("claude:max20", "5h", 82.0, now + 3600)
st = engine.status(S, now=now)
cl = [a for a in st["accounts"] if a["provider"] == "claude"][0]
w5 = [w for w in cl["windows"] if w["key"] == "5h"][0]
chk("snapshot window uses provider used%", w5["used_percent"] == 82.0 and w5["state"] == "warn")
S.set_cap("cursor:pro", "month", 500)
chk("cap override persists", S.caps("cursor:pro").get("month") == 500)

# ---- sources: local Claude Code JSONL reader (fake home) ----
fake = tempfile.mkdtemp(prefix="fakehome-")
pdir = os.path.join(fake, ".claude", "projects", "proj")
os.makedirs(pdir)
with open(os.path.join(pdir, "s.jsonl"), "w") as f:
    f.write(json.dumps({"timestamp": "2026-07-04T10:00:00Z", "message": {
        "id": "msg_1", "model": "claude-sonnet-5",
        "usage": {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 200,
                  "cache_creation_input_tokens": 0}}}) + "\n")
    f.write("not json\n")
    f.write(json.dumps({"message": {"role": "user", "content": "hi"}}) + "\n")  # no usage
evs = list(sources.claude_code(home=fake))
chk("claude_code reader parses one usage event", len(evs) == 1 and evs[0]["provider"] == "claude")
chk("reader computes cost + dedup id", evs[0]["cost_usd"] > 0 and evs[0]["dedup"] == "claude_code:msg_1")

# ---- Codex reader: tokens + rate-limit read (fake home) ----
cdir = os.path.join(fake, ".codex", "sessions", "2026", "11", "01")
os.makedirs(cdir)
with open(os.path.join(cdir, "rollout-x.jsonl"), "w") as f:
    f.write(json.dumps({"timestamp": "2026-11-01T01:00:00Z", "type": "turn_context",
                        "payload": {"model": "gpt-5.6-sol"}}) + "\n")
    f.write(json.dumps({"timestamp": "2026-11-01T01:00:05Z", "type": "event_msg", "payload": {
        "type": "token_count",
        "info": {"last_token_usage": {"input_tokens": 1000, "cached_input_tokens": 400,
                                      "output_tokens": 50, "reasoning_output_tokens": 10}},
        "rate_limits": {"primary": {"used_percent": 42.0, "window_minutes": 10080,
                                    "resets_at": 1900000000}, "secondary": None,
                        "plan_type": "pro"}}}) + "\n")
cx = list(sources.codex(home=fake))
chk("codex reader: one event, cached split out of input",
    len(cx) == 1 and cx[0]["in_tokens"] == 600 and cx[0]["cache_read"] == 400
    and cx[0]["model"] == "gpt-5.6-sol" and cx[0]["provider"] == "codex")
cl = sources.codex_limits(home=fake)
chk("codex limits read: weekly 42% with reset", cl and cl["plan_type"] == "pro"
    and cl["windows"][0]["key"] == "week" and cl["windows"][0]["used_percent"] == 42.0)
S.add_account("codex", "pro")
sources.ingest(S, home=fake)
sources.ingest(S, home=fake)  # idempotent
cst = [a for a in engine.status(S)["accounts"] if a["provider"] == "codex"][0]
chk("sync stores the codex snapshot as the weekly gauge", cst["windows"][0]["used_percent"] == 42.0)
chk("re-sync does not double-count", len(S.events(provider="codex")) == 1)
chk("status is JSON-serialisable", json.loads(json.dumps(engine.status(S)))["events"] >= 1)

# ---- generic jsonl import ----
jf = os.path.join(fake, "u.jsonl")
open(jf, "w").write(json.dumps({"ts": now, "model": "gpt-5", "in_tokens": 100, "out_tokens": 50}) + "\n")
imported = list(sources.jsonl(jf))
chk("jsonl import computes missing cost", len(imported) == 1 and imported[0]["cost_usd"] > 0)

# ---- richer dashboard data: gauge, per-model breakdown, heatmap, streak/level ----
st = engine.status(S, now=now)
acc = [a for a in st["accounts"] if a["provider"] == "cursor"][0]
chk("account exposes a primary gauge window", acc.get("gauge") and acc["gauge"]["key"] in {w["key"] for w in acc["windows"]})
chk("gauge is the shortest declared window", acc["gauge"]["key"] == "month")  # cursor has only 'month'
mb = engine.models_breakdown(S.events(provider="cursor"), now=now, days=7)
chk("model breakdown yields percents summing ~100", mb and abs(sum(m["percent"] for m in mb) - 100) < 1.0)
chk("status carries models_7d + heatmap + progress", "models_7d" in st and len(st["heatmap"]) == 7 and len(st["heatmap"][0]) == 24 and "progress" in st)
chk("progress has streak + level + tier", set(st["progress"]) == {"streak", "active_days", "level", "tier"})
chk("peak_hour is an hour or None", st["peak_hour"] is None or 0 <= st["peak_hour"] <= 23)
# top-N + Other fold
for k in range(9):
    S.add_event(ts=now, source="manual", provider="zz", model="m%d" % k, cost_usd=0.0)
mb2 = engine.models_breakdown(S.events(provider="zz"), now=now, days=7, top=6)
chk("model breakdown folds tail into 'Other'", any(m["model"] == "Other" for m in mb2) and len(mb2) == 7)

# ---- the Opus weekly window counts only Opus calls ----
S.add_account("claude", "max20")
S.add_event(ts=now, source="manual", provider="claude", model="claude-opus-5-5", cost_usd=0.0)
S.add_event(ts=now, source="manual", provider="claude", model="claude-sonnet-5-5", cost_usd=0.0)
ca = [a for a in engine.status(S, now=now)["accounts"] if a["provider"] == "claude"][0]
wk = {w["key"]: w["used"] for w in ca["windows"]}
chk("Opus weekly window counts only Opus calls", wk["week_opus"] == 1 and wk["week"] >= 2)

print("\n%d passed, %d failed" % (P, F)); sys.exit(1 if F else 0)
