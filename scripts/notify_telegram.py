#!/usr/bin/env python3
"""
G8 PORT — Telegram notifier.  Stdlib only.  Secrets from env:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Modes
  python3 scripts/notify_telegram.py --states data/state/pos_g8.json [more.json ...]
      Sends ONE message only if some state has a non-empty diff_vs_prev or dqm.alerts (D6).
      Exit 0 always (a notifier must never break the pipeline).
  python3 scripts/notify_telegram.py --fail "text"
      Sends a pipeline-failure alert (principle 9).
  python3 scripts/notify_telegram.py --ping
      Sends a test message.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def send(text):
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not cid:
        print("telegram: secrets missing, message not sent:\n" + text)
        return
    body = urllib.parse.urlencode({"chat_id": cid, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % tok, data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("telegram:", r.status)
    except Exception as e:  # never fail the pipeline because of the notifier
        print("telegram error:", e)


def fmt_state(st):
    m, o, d, j = st["meta"], st["outputs"], st["dqm"], st["journal_link"]
    lines = ["<b>%s</b> · %s · health %.0f" % (m["script"], o["regime"]["label"], d["health_score"])]
    changed = [k for k in j["diff_vs_prev"] if k in o["per_ccy"] or k in o["pairs"]]
    for k in changed:
        v = o["per_ccy"].get(k) or o["pairs"].get(k)
        lines.append("  %s → %s (z %s)" % (k, v.get("state") or v.get("direction"), v.get("z")))
    for k in j["diff_vs_prev"]:
        if k not in o["per_ccy"] and k not in o["pairs"]:
            lines.append("  · %s" % k)
    for a in d["alerts"]:
        lines.append("  ⚠ " + a)
    if m["twin_test"]["status"] != "PASSED":
        lines.append("  twin-test: %s (not funnel-eligible)" % m["twin_test"]["status"])
    return "\n".join(lines)


def main(argv):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if "--ping" in argv:
        send("G8 pipeline · ping OK · %s" % now)
        return 0
    if "--fail" in argv:
        txt = argv[argv.index("--fail") + 1] if len(argv) > argv.index("--fail") + 1 else "unknown"
        run = os.environ.get("GITHUB_RUN_ID", "local")
        send("🔴 <b>G8 pipeline FAILED</b> · %s\n%s\nrun %s" % (now, txt, run))
        return 0
    if "--states" in argv:
        paths = argv[argv.index("--states") + 1:]
        blocks = []
        for p in paths:
            try:
                st = json.load(open(p))
            except Exception as e:
                blocks.append("⚠ cannot read %s: %s" % (p, e))
                continue
            if st["journal_link"]["diff_vs_prev"] or st["dqm"]["alerts"]:
                blocks.append(fmt_state(st))
        if blocks:
            send("G8 · %s\n\n" % now + "\n\n".join(blocks))
        else:
            print("telegram: no state change, nothing sent (D6)")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
