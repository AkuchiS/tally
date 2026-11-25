"""Per-model price table + cost formula.

Prices are USD per MILLION tokens (input / output / cache-read / cache-write), matched by
longest substring — so "claude-opus-4-8-20260101" resolves to the "opus-4-8" entry. The
cost formula mirrors the standard token-cost math (input + output + cache_read +
cache_write + reasoning), all divided by 1e6. Update TABLE as list prices change; unknown
models cost 0 (and are reported as "unpriced" rather than silently wrong).

PROVENANCE — every Anthropic row below was re-checked against from the vendor's own published page:
  source:    https://platform.claude.com/docs/en/about-claude/pricing
  retrieved: 2026-11-25
These are the vendor's list prices — public facts, taken from the vendor, not from any other
tracker's compilation. Re-derive from that page (and re-stamp the date) when prices move;
a stale price table silently misreports spend, which is the one thing this tool must not do.
Other vendors' rows are from each vendor's own pricing page.
"""
import datetime

# match-key -> {in, out, cr (cache read), cw (cache write)} in USD / 1M tokens.
# Lookup picks the LONGEST matching key, so ordering here is for humans, not the algorithm.
TABLE = {
    # --- Anthropic (Claude) — platform.claude.com/docs/en/about-claude/pricing, 2026-07-17 ---
    "fable-5-2":  {"in": 8.0,  "out": 40.0, "cr": 0.20, "cw": 10.0},
    "fable-5-1":  {"in": 10.0, "out": 50.0, "cr": 0.25, "cw": 12.5},
    "fable-5":    {"in": 10.0, "out": 50.0, "cr": 1.00, "cw": 12.5},
    "mythos-5-2": {"in": 8.0,  "out": 40.0, "cr": 0.20, "cw": 10.0},
    "mythos-5-1": {"in": 10.0, "out": 50.0, "cr": 0.25, "cw": 12.5},
    "mythos-5":   {"in": 10.0, "out": 50.0, "cr": 1.00, "cw": 12.5},
    "opus-5-5":   {"in": 4.0,  "out": 20.0, "cr": 0.20, "cw": 5.0},
    "opus-5":     {"in": 5.0,  "out": 25.0, "cr": 0.50, "cw": 6.25},
    "sonnet-5-5": {"in": 2.0,  "out": 10.0, "cr": 0.10, "cw": 2.50},
    "haiku-5-5":  {"in": 0.10, "out": 0.50, "cr": 0.01, "cw": 0.125},  # prompts <=100k; see TIERED
    "opus-4-8":   {"in": 5.0,  "out": 25.0, "cr": 0.50, "cw": 6.25},
    "opus-4-7":   {"in": 5.0,  "out": 25.0, "cr": 0.50, "cw": 6.25},
    "opus-4-6":   {"in": 5.0,  "out": 25.0, "cr": 0.50, "cw": 6.25},
    "opus-4-5":   {"in": 5.0,  "out": 25.0, "cr": 0.50, "cw": 6.25},
    "opus-4-1":   {"in": 15.0, "out": 75.0, "cr": 1.50, "cw": 18.75},
    "opus-4":     {"in": 15.0, "out": 75.0, "cr": 1.50, "cw": 18.75},  # retired; NOT the $5 tier
    "opus":       {"in": 5.0,  "out": 25.0, "cr": 0.50, "cw": 6.25},
    "sonnet-5":   {"in": 2.0,  "out": 10.0, "cr": 0.20, "cw": 2.50},   # $2/$10 is now the standard price
    "sonnet-4-6": {"in": 3.0,  "out": 15.0, "cr": 0.30, "cw": 3.75},
    "sonnet-4-5": {"in": 3.0,  "out": 15.0, "cr": 0.30, "cw": 3.75},
    "sonnet-4":   {"in": 3.0,  "out": 15.0, "cr": 0.30, "cw": 3.75},
    "sonnet":     {"in": 3.0,  "out": 15.0, "cr": 0.30, "cw": 3.75},
    "haiku-4-5":  {"in": 1.0,  "out": 5.0,  "cr": 0.10, "cw": 1.25},
    "haiku-3-5":  {"in": 0.80, "out": 4.0,  "cr": 0.08, "cw": 1.00},
    "haiku":      {"in": 1.0,  "out": 5.0,  "cr": 0.10, "cw": 1.25},
    # --- OpenAI (ChatGPT / Codex) ---
    "gpt-5.5-pro": {"in": 30.0, "out": 180.0, "cr": 0.0},
    "gpt-5.5":     {"in": 5.0,  "out": 30.0,  "cr": 0.50},
    "gpt-5.4-mini":{"in": 0.75, "out": 4.50,  "cr": 0.075},
    "gpt-5.4":     {"in": 2.50, "out": 15.0,  "cr": 0.25},
    "gpt-5.1-codex":{"in": 1.25,"out": 10.0,  "cr": 0.125},
    "gpt-5.1":     {"in": 0.625,"out": 5.0,   "cr": 0.125},
    "gpt-5-mini":  {"in": 0.125,"out": 1.0,   "cr": 0.025},
    "gpt-5-nano":  {"in": 0.05, "out": 0.40,  "cr": 0.005},
    "gpt-5":       {"in": 1.25, "out": 10.0,  "cr": 0.125},
    "gpt-4.1-mini":{"in": 0.40, "out": 1.60,  "cr": 0.10},
    "gpt-4.1":     {"in": 2.0,  "out": 8.0,   "cr": 0.50},
    "gpt-4o":      {"in": 2.50, "out": 10.0,  "cr": 1.25},
    "o4-mini":     {"in": 1.10, "out": 4.40,  "cr": 0.55},
    "o3":          {"in": 2.0,  "out": 8.0,   "cr": 0.50},
    # --- Google (Gemini) ---
    "gemini-2.5-pro":   {"in": 1.25, "out": 10.0, "cr": 0.31},
    "gemini-2.5-flash": {"in": 0.30, "out": 2.50, "cr": 0.075},
    "gemini-2.0-flash": {"in": 0.10, "out": 0.40, "cr": 0.025},
    "gemini":           {"in": 1.25, "out": 10.0, "cr": 0.31},
    # --- xAI (Grok) ---
    "grok-4":  {"in": 3.0, "out": 15.0, "cr": 0.75},
    "grok-3":  {"in": 3.0, "out": 15.0, "cr": 0.75},
    "grok":    {"in": 3.0, "out": 15.0, "cr": 0.75},
    # --- Perplexity (sonar) ---
    "sonar-pro": {"in": 3.0, "out": 15.0, "cr": 0.0},
    "sonar":     {"in": 1.0, "out": 1.0,  "cr": 0.0},
    # --- others commonly seen ---
    "mistral-large": {"in": 2.0, "out": 6.0, "cr": 0.0},
    "deepseek":      {"in": 0.27, "out": 1.10, "cr": 0.07},
}

ZERO = {"in": 0.0, "out": 0.0, "cr": 0.0, "cw": 0.0}

# Price changes the vendor has ANNOUNCED but that have not started yet. A flat table cannot
# express these, and the failure is silent: Anthropic publishes Sonnet 5 at $2/$10 "through
# August 31, 2026" and $3/$15 "starting September 1, 2026", so a flat $2/$10 under-reports
# every Sonnet 5 call by a third from that date — in the one tool whose job is to warn you
# before you hit a wall. key -> [(iso_start_date, prices), ...]; the latest one that has
# started wins. Source + retrieval date as in the module docstring.
SCHEDULED = {}   # Sonnet 5's announced rise to $3/$15 was cancelled by the vendor (checked 2026-11-25).

# Prompt-size surcharge tiers: key -> (threshold in prompt tokens, prices when prompt is over it).
# Haiku 5.5: a request whose prompt (input + cache reads + cache writes) exceeds 100,000 tokens
# is billed at the higher rate.
TIERED = {
    "haiku-5-5": (100_000, {"in": 0.50, "out": 2.50, "cr": 0.05, "cw": 0.625}),
}


def _effective(key, on=None):
    """TABLE[key], with any announced change that has taken effect by `on` applied."""
    prices = TABLE[key]
    for start, changed in sorted(SCHEDULED.get(key, [])):
        if (on or datetime.date.today().isoformat()) >= start:
            prices = changed
    return prices


def _best_key(model):
    m = (model or "").lower()
    best = None
    for key in TABLE:
        if key in m and (best is None or len(key) > len(best)):
            best = key
    return best


def price_for(model, on=None):
    """Longest-substring match into TABLE. Returns a price dict (ZERO if unknown).

    `on` is an ISO date (default: today) — the price *in force* then. Pass it to price a
    historical call correctly, or to test a scheduled change before it lands.
    """
    m = (model or "").lower()
    if not m:
        return dict(ZERO)
    best = None
    for key in TABLE:
        if key in m and (best is None or len(key) > len(best)):
            best = key
    return {**ZERO, **_effective(best, on)} if best else dict(ZERO)


def is_priced(model):
    return price_for(model) != ZERO


def cost(model, in_tok=0, out_tok=0, cache_read_tok=0, cache_write_tok=0, reasoning_tok=0,
         reasoning_in_output=False, on=None):
    """USD cost of a single call. `reasoning_in_output=True` (Codex-style) means reasoning
    tokens are already billed inside output — don't double-count them. `on` (ISO date) prices
    the call at the rate in force then; defaults to today."""
    p = price_for(model, on)
    key = _best_key(model)
    if key in TIERED:
        limit, over = TIERED[key]
        if (in_tok or 0) + (cache_read_tok or 0) + (cache_write_tok or 0) > limit:
            p = {**ZERO, **over}
    rc = 0.0 if reasoning_in_output else (reasoning_tok or 0) * p["out"]
    return (
        (in_tok or 0) * p["in"]
        + (out_tok or 0) * p["out"]
        + (cache_read_tok or 0) * p["cr"]
        + (cache_write_tok or 0) * p["cw"]
        + rc
    ) / 1_000_000.0
