"""Local usage store — one SQLite file under ~/.tally, plus account + snapshot state.

Everything tally knows lives in ONE local file (no server, no cloud). `events` is the
append-only usage ledger; `accounts` is which subscriptions/API keys you track; `snapshots`
is the last-good provider-supplied window read (kept so a transient 429 doesn't blank the
panel — a resilience trick lifted from the trackers we studied). stdlib-only, fail-soft.
"""
import json
import os
import sqlite3
import time

_CONN = {}


def data_dir():
    d = os.environ.get("TALLY_HOME") or os.path.join(os.path.expanduser("~"), ".tally")
    os.makedirs(d, exist_ok=True)
    return d


def db_path():
    return os.path.join(data_dir(), "tally.db")


def _db():
    p = db_path()
    c = _CONN.get(p)
    if c is None:
        c = sqlite3.connect(p, check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=4000")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY, ts REAL, source TEXT, provider TEXT, model TEXT,
            kind TEXT, in_tokens INTEGER, out_tokens INTEGER, cache_read INTEGER,
            cache_write INTEGER, reasoning INTEGER, cost_usd REAL, dedup TEXT, meta TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_events_dedup ON events(dedup) WHERE dedup IS NOT NULL;
        CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
        CREATE TABLE IF NOT EXISTS accounts(
            id TEXT PRIMARY KEY, provider TEXT, plan TEXT, label TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS snapshots(
            account_id TEXT, window_key TEXT, used_percent REAL, reset_at REAL, at REAL,
            PRIMARY KEY(account_id, window_key));
        CREATE TABLE IF NOT EXISTS caps(
            account_id TEXT, window_key TEXT, cap REAL, PRIMARY KEY(account_id, window_key));
        """)
        c.commit()
        _CONN[p] = c
    return c


def reset():
    """Close + delete the store (tests)."""
    p = db_path()
    c = _CONN.pop(p, None)
    if c:
        c.close()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(p + suffix)
        except OSError:
            pass


def add_event(ts=None, source="manual", provider="", model="", kind="api", in_tokens=0,
              out_tokens=0, cache_read=0, cache_write=0, reasoning=0, cost_usd=None,
              dedup=None, meta=None):
    """Append one usage event. `dedup` (e.g. a source line id) makes re-imports idempotent."""
    c = _db()
    ts = ts if ts is not None else time.time()
    try:
        cur = c.execute(
            "INSERT OR IGNORE INTO events(ts,source,provider,model,kind,in_tokens,out_tokens,"
            "cache_read,cache_write,reasoning,cost_usd,dedup,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, source, provider, model, kind, in_tokens, out_tokens, cache_read, cache_write,
             reasoning, cost_usd, dedup, json.dumps(meta) if meta else None))
        c.commit()
        # rowcount is 0 when a dup dedup key was IGNOREd — return None so callers that count
        # "newly added" don't over-count on re-import (lastrowid stays truthy on ignore).
        return cur.lastrowid if cur.rowcount else None
    except Exception:
        return None


def events(since=None, until=None, provider=None, source=None):
    c = _db()
    q = "SELECT ts,source,provider,model,kind,in_tokens,out_tokens,cache_read,cache_write,reasoning,cost_usd,meta FROM events WHERE 1=1"
    args = []
    if since is not None:
        q += " AND ts>=?"; args.append(since)
    if until is not None:
        q += " AND ts<=?"; args.append(until)
    if provider:
        q += " AND provider=?"; args.append(provider)
    if source:
        q += " AND source=?"; args.append(source)
    q += " ORDER BY ts"
    cols = ("ts", "source", "provider", "model", "kind", "in_tokens", "out_tokens",
            "cache_read", "cache_write", "reasoning", "cost_usd", "meta")
    out = []
    for row in c.execute(q, args):
        d = dict(zip(cols, row))
        d["meta"] = json.loads(d["meta"]) if d["meta"] else {}
        out.append(d)
    return out


def add_account(provider, plan, label="", account_id=None):
    c = _db()
    aid = account_id or ("%s:%s" % ((provider or "").lower(), (plan or "").lower()))
    c.execute("INSERT OR REPLACE INTO accounts(id,provider,plan,label,created) VALUES(?,?,?,?,?)",
              (aid, (provider or "").lower(), (plan or "").lower(), label, time.time()))
    c.commit()
    return aid


def accounts():
    c = _db()
    return [dict(zip(("id", "provider", "plan", "label", "created"), r))
            for r in c.execute("SELECT id,provider,plan,label,created FROM accounts ORDER BY created")]


def remove_account(account_id):
    c = _db()
    c.execute("DELETE FROM accounts WHERE id=?", (account_id,))
    c.commit()


def set_cap(account_id, window_key, cap):
    c = _db()
    c.execute("INSERT OR REPLACE INTO caps(account_id,window_key,cap) VALUES(?,?,?)",
              (account_id, window_key, cap))
    c.commit()


def caps(account_id):
    c = _db()
    return {wk: cap for wk, cap in c.execute(
        "SELECT window_key,cap FROM caps WHERE account_id=?", (account_id,))}


def set_snapshot(account_id, window_key, used_percent, reset_at):
    """Store a provider-supplied window read (kept as last-good)."""
    c = _db()
    c.execute("INSERT OR REPLACE INTO snapshots(account_id,window_key,used_percent,reset_at,at) "
              "VALUES(?,?,?,?,?)", (account_id, window_key, used_percent, reset_at, time.time()))
    c.commit()


def snapshots(account_id):
    c = _db()
    return {wk: {"used_percent": up, "reset_at": ra, "at": at}
            for wk, up, ra, at in c.execute(
                "SELECT window_key,used_percent,reset_at,at FROM snapshots WHERE account_id=?",
                (account_id,))}
