"""tally CLI — the human surface. `tally <command>`; no config needed to start.

  status                 show every tracked limit + spend (--json prints JSON, --out FILE saves it)
  sync                   read local tool logs (Claude Code, Codex) into the ledger
  track <provider> <plan>   start tracking a subscription (e.g. tally track claude max20)
  untrack <account_id>
  add <provider> <model> --in N --out N [--cache-read N] [--cache-write N]   record a call
  import <file.jsonl>    import a generic usage file
  cap <account_id> <window> <n>       set a window's cap (enables the % bar)
  snapshot <account_id> <window> <pct> [reset_epoch]   store a provider-supplied read
  watch [--interval S]   live status loop
  serve [--port P]       open the local web dashboard
"""
import argparse
import json
import time

from . import store as S, sources, engine, __version__


def _fmt_pct(w):
    p = w["used_percent"]
    return ("%.0f%%" % p) if p is not None else ("%s %s" % (w["used"], w["unit"]))


def _reset_in(w, now):
    if not w.get("reset_at"):
        return "?"
    mins = max(0, int((w["reset_at"] - now) / 60))
    return "%dh%02dm" % (mins // 60, mins % 60) if mins >= 60 else "%dm" % mins


_ICON = {"ok": "🟢", "warn": "🟡", "critical": "🟠", "over": "🔴", "unknown": "⚪"}


def cmd_status(args):
    st = engine.status(S)
    if args.json or getattr(args, "out", None):
        text = json.dumps(st, indent=2)
        if getattr(args, "out", None):
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            print("wrote %s" % args.out)
        else:
            print(text)
        return 0
    now = st["now"]
    sp = st["spend"]
    print("tally · spend  today $%.2f · week $%.2f · month $%.2f (→ ~$%.2f)" % (
        sp["today"], sp["week"], sp["month"], sp["forecast_month"]))
    if not st["accounts"]:
        print("  (no subscriptions tracked yet — `tally track claude max20`, `tally sync`)")
    for a in st["accounts"]:
        print("  %s  [%s/%s]" % (a["label"] or a["id"], a["provider"], a["plan"]))
        for w in a["windows"]:
            print("    %s %-18s %6s   resets %s" % (
                _ICON.get(w["state"], "⚪"), w["label"], _fmt_pct(w), _reset_in(w, now)))
    al = engine.alerts(S)
    if al:
        print("\n  ⚠ %d approaching a wall:" % len(al))
        for x in al[:5]:
            print("    %s %s" % (_ICON.get(x["state"]), x["message"]))
    if st["unpriced"]:
        print("\n  (unpriced models — add to pricing.TABLE: %s)" % ", ".join(st["unpriced"][:5]))
    return 0


def cmd_sync(args):
    n = sources.ingest(S)
    print("synced %d new events" % n)
    return 0


def cmd_track(args):
    aid = S.add_account(args.provider, args.plan, label=args.label or "")
    print("tracking %s" % aid)
    return 0


def cmd_untrack(args):
    S.remove_account(args.account_id)
    print("untracked %s" % args.account_id)
    return 0


def cmd_add(args):
    from . import pricing
    cost = pricing.cost(args.model, args.in_tok, args.out_tok, args.cache_read, args.cache_write)
    S.add_event(source="manual", provider=args.provider, model=args.model, kind="api",
                in_tokens=args.in_tok, out_tokens=args.out_tok, cache_read=args.cache_read,
                cache_write=args.cache_write, cost_usd=cost)
    print("recorded %s call ($%.4f)" % (args.model, cost))
    return 0


def cmd_import(args):
    n = 0
    for ev in sources.jsonl(args.file):
        if S.add_event(**ev) is not None:
            n += 1
    print("imported %d events from %s" % (n, args.file))
    return 0


def cmd_cap(args):
    S.set_cap(args.account_id, args.window, args.cap)
    print("cap set: %s/%s = %g" % (args.account_id, args.window, args.cap))
    return 0


def cmd_snapshot(args):
    S.set_snapshot(args.account_id, args.window, args.pct,
                   args.reset if args.reset else time.time() + 5 * 3600)
    print("snapshot: %s/%s = %g%%" % (args.account_id, args.window, args.pct))
    return 0


def cmd_watch(args):
    try:
        while True:
            print("\x1b[2J\x1b[H", end="")
            cmd_status(args)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def cmd_serve(args):
    from . import dashboard
    dashboard.serve(port=args.port)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="tally", description="Track every AI limit and dollar, locally.")
    p.add_argument("--version", action="version", version="tally %s" % __version__)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("status"); s.add_argument("--json", action="store_true")
    s.add_argument("--out", help="write the JSON status to this file (for dashboards)"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("sync"); s.set_defaults(fn=cmd_sync)
    s = sub.add_parser("track"); s.add_argument("provider"); s.add_argument("plan")
    s.add_argument("--label", default=""); s.set_defaults(fn=cmd_track)
    s = sub.add_parser("untrack"); s.add_argument("account_id"); s.set_defaults(fn=cmd_untrack)
    s = sub.add_parser("add"); s.add_argument("provider"); s.add_argument("model")
    s.add_argument("--in", dest="in_tok", type=int, default=0)
    s.add_argument("--out", dest="out_tok", type=int, default=0)
    s.add_argument("--cache-read", dest="cache_read", type=int, default=0)
    s.add_argument("--cache-write", dest="cache_write", type=int, default=0); s.set_defaults(fn=cmd_add)
    s = sub.add_parser("import"); s.add_argument("file"); s.set_defaults(fn=cmd_import)
    s = sub.add_parser("cap"); s.add_argument("account_id"); s.add_argument("window")
    s.add_argument("cap", type=float); s.set_defaults(fn=cmd_cap)
    s = sub.add_parser("snapshot"); s.add_argument("account_id"); s.add_argument("window")
    s.add_argument("pct", type=float); s.add_argument("reset", type=float, nargs="?"); s.set_defaults(fn=cmd_snapshot)
    s = sub.add_parser("watch"); s.add_argument("--interval", type=float, default=5.0)
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_watch)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8848); s.set_defaults(fn=cmd_serve)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not getattr(args, "fn", None):
        build_parser().print_help()
        return 0
    return args.fn(args)
