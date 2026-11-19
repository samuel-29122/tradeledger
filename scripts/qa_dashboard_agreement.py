"""Phase 6 QA: does everything the dashboard shows agree with an
INDEPENDENT recalculation from the raw database rows?

An audit, not part of pytest (it takes ~15 s and needs Node.js). It builds
a throwaway database (the sample data plus 600 random trades in three
currencies, 5% open, 3% with no exit time), recomputes every figure from
the raw SQL rows with its OWN code (it never calls the engine), then
compares four outputs against that for every filter combination:
  1. the HTML page (KPI cards, money strip, all detailed tables)
  2. the chart payload embedded in the page
  3. the JSON API (/api/v1/analytics/*)
  4. the tooltip text produced by the REAL analytics.js (run in Node)

Phase 8: the audited account (user 1) shares the database with a SECOND
account holding 120 decoy trades on instruments with the same symbols. The
independent recalculation reads only user 1's rows, so any leak of the
other account's data into the dashboard or the API is a disagreement.
The page and the API are requested as user 1 (signed-in session; API token).

Usage (from the project folder):
    python scripts/qa_dashboard_agreement.py            # finds node on PATH
    python scripts/qa_dashboard_agreement.py --node /path/to/node
    python scripts/qa_dashboard_agreement.py --database-url postgresql://...  # audit on PostgreSQL
      (add --pg-bridge to use the test-only libpq bridge where psycopg is not installed)

Exit code 0 = no disagreements. Precision trap: every Decimal sum here runs
inside localcontext(prec=80); Python's default context keeps only 28 digits.
"""

import itertools
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from html import unescape
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from tradeledger import create_app  # noqa: E402
from tradeledger.db import get_db, init_schema, register_postgres_driver  # noqa: E402
from tradeledger.passwords import HashParameters, hash_password  # noqa: E402
from tradeledger.repositories import api_tokens, sessions  # noqa: E402
from tradeledger.repositories.instruments import create_instrument  # noqa: E402
from tradeledger.repositories.trades import create_trade  # noqa: E402
from tradeledger.repositories.users import create_user  # noqa: E402
from tradeledger.seed import seed_sample_data  # noqa: E402

AUDITED = 1  # the audited account's id (created first)
FAST = HashParameters(memory_kib=1024, iterations=1, lanes=1)

TOL = Decimal("1e-30")
SESSION_LABELS = {
    "ASIA": "Asia",
    "LONDON": "London",
    "NEW_YORK": "New York",
    "OTHER": "Other",
    None: "Unspecified",
}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
problems = []
checks = 0


def check(condition, message):
    global checks
    checks += 1
    if not condition:
        problems.append(message)


# ---------- My own formatting (written from the documented rules) ----------


def q(value, places):
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def fmt_r(v):
    if v is None:
        return "n/a"
    r = q(v, "0.01")
    return "0.00R" if r == 0 else f"{r:+}R"


def fmt_rate(wins, total):
    return "n/a" if total == 0 else f"{q(Decimal(wins) * 100 / Decimal(total), '0.1')}%"


def fmt_ratio(v):
    return "n/a" if v is None else str(q(v, "0.01"))


# ---------- Build a database: sample data + 600 random trades ----------


def random_trade(rnd, instrument_ids, strategies, sessions):
    direction = rnd.choice(["LONG", "SHORT"])
    entry = Decimal(rnd.randint(5000, 20000)) / 100
    risk = Decimal(rnd.randint(10, 300)) / 100
    stop = entry - risk if direction == "LONG" else entry + risk
    move = Decimal(rnd.randint(-400, 400)) / 100
    exit_price = max(Decimal("0.01"), entry + move)
    start = datetime(2025, 11, 1, tzinfo=UTC)
    entered = start + (datetime(2026, 4, 30, tzinfo=UTC) - start) * rnd.random()
    entered = entered.replace(microsecond=0)
    closed = entered.timestamp() + rnd.randint(60, 5 * 86400)
    closed = datetime.fromtimestamp(closed, UTC)
    is_open = rnd.random() < 0.05
    return {
        "instrument_id": rnd.choice(instrument_ids),
        "direction": direction,
        "status": "OPEN" if is_open else "CLOSED",
        "trade_date": entered.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "closed_at": None if is_open else closed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entry_price": str(entry),
        "stop_loss": str(stop),
        "exit_price": None if is_open else str(exit_price),
        "position_size": str(rnd.choice(["1", "2", "0.5", "3"])),
        "fees": str(Decimal(rnd.randint(0, 300)) / 100),
        "strategy": rnd.choice(strategies),
        "session": rnd.choice(sessions),
    }


def build_database(target):
    app = create_app("testing", overrides={"DATABASE": target})
    rnd = random.Random(20261115)
    with app.app_context():
        conn = get_db()
        init_schema(conn)
        audited = create_user(conn, "audited@example.com", hash_password("audit password 1", FAST))
        decoy = create_user(conn, "decoy@example.com", hash_password("audit password 2", FAST))
        if audited != AUDITED:
            raise SystemExit("the audit needs an empty database (audited account must be id 1)")
        seed_sample_data(conn, user_id=audited)
        # The decoy account: same symbols, very different trades.
        seed_sample_data(conn, user_id=decoy)
        decoy_instruments = [
            r[0] for r in conn.execute("SELECT id FROM instruments WHERE user_id = ?", (decoy,))
        ]
        for _ in range(120):
            create_trade(
                conn,
                random_trade(rnd, decoy_instruments, ["Breakout", "Decoy"], ["ASIA", None]),
                user_id=decoy,
            )
        extra = [
            create_instrument(
                conn,
                {
                    "symbol": sym,
                    "name": sym,
                    "asset_class": "OTHER",
                    "price_increment": "0.01",
                    "contract_multiplier": mult,
                    "quote_currency": cur,
                },
                user_id=audited,
            )
            for sym, mult, cur in (("QAUSD", "10", "USD"), ("QAGBP", "1", "GBP"), ("QAEUR", "0.5", "EUR"))
        ]
        strategies = ["Breakout", "Pullback", "Scalp", "Fade", None]
        sessions = ["ASIA", "LONDON", "NEW_YORK", "OTHER", None]
        for _ in range(600):
            create_trade(conn, random_trade(rnd, extra, strategies, sessions), user_id=audited)
        # 3% legacy trades: exit time never recorded.
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM trades WHERE status='CLOSED' AND user_id = ? AND id > 16 ORDER BY id",
                (audited,),
            )
        ]
        for trade_id in rnd.sample(ids, len(ids) * 3 // 100):
            conn.execute("UPDATE trades SET closed_at = NULL WHERE id = ?", (trade_id,))
        conn.commit()
    return app


# ---------- Independent recalculation from raw rows ----------


def raw_rows(app):
    with app.app_context():
        return [
            dict(r)
            for r in get_db().execute(
                "SELECT t.*, i.symbol, i.contract_multiplier, i.quote_currency "
                "FROM trades t JOIN instruments i ON i.id = t.instrument_id WHERE t.user_id = ?",
                (AUDITED,),
            )
        ]


def independent(rows, f):
    with localcontext() as ctx:
        ctx.prec = 80
        out = []
        open_count = 0
        for t in rows:
            if f["instrument_id"] and t["instrument_id"] != f["instrument_id"]:
                continue
            if f["strategy"] and t["strategy"] != f["strategy"]:
                continue
            if f["session"] and t["session"] != f["session"]:
                continue
            if t["status"] == "OPEN":
                open_count += 1 if not (f["start"] or f["end"]) else 0
                continue
            realised = t["closed_at"] or t["trade_date"]
            day = realised[:10]
            if (f["start"] and day < f["start"]) or (f["end"] and day > f["end"]):
                continue
            sign = 1 if t["direction"] == "LONG" else -1
            entry, stop, exit_ = (Decimal(t[k]) for k in ("entry_price", "stop_loss", "exit_price"))
            size, mult = Decimal(t["position_size"]), Decimal(t["contract_multiplier"])
            net = (exit_ - entry) * sign * size * mult - Decimal(t["fees"] or "0")
            risk = abs(entry - stop) * size * mult
            out.append(
                {
                    "id": t["id"],
                    "when": realised,
                    "symbol": t["symbol"],
                    "net": net,
                    "r": net / risk,
                    "cur": t["quote_currency"],
                    "strategy": t["strategy"],
                    "session": t["session"],
                }
            )
        out.sort(key=lambda x: (x["when"], x["id"]))
        return out, open_count


def summary(trades):
    with localcontext() as ctx:
        ctx.prec = 80
        n = len(trades)
        wins = sum(1 for t in trades if t["net"] > 0)
        losses = sum(1 for t in trades if t["net"] < 0)
        total = sum((t["r"] for t in trades), Decimal(0))
        gains = sum((t["r"] for t in trades if t["r"] > 0), Decimal(0))
        pains = -sum((t["r"] for t in trades if t["r"] < 0), Decimal(0))
        cum, peak, worst = Decimal(0), Decimal(0), Decimal(0)
        for t in trades:
            cum += t["r"]
            peak = max(peak, cum)
            worst = max(worst, peak - cum)
        streaks = {"WIN": 0, "LOSS": 0}
        run_kind, run = None, 0
        for t in trades:
            kind = "WIN" if t["net"] > 0 else "LOSS" if t["net"] < 0 else None
            run = run + 1 if kind and kind == run_kind else (1 if kind else 0)
            run_kind = kind
            if kind:
                streaks[kind] = max(streaks[kind], run)
        return {
            "trades": n,
            "wins": wins,
            "losses": losses,
            "breakevens": n - wins - losses,
            "total_r": total,
            "mean_r": total / n if n else None,
            "pf": gains / pains if pains else None,
            "max_dd": worst,
            "win_streak": streaks["WIN"],
            "loss_streak": streaks["LOSS"],
        }


def grouped(trades, key):
    groups = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    return {k: summary(v) for k, v in groups.items()}


# ---------- Reading the page ----------


def kpis(html):
    return dict(re.findall(r'data-kpi="([a-z_]+)">.*?class="kpi__value">([^<]*)<', html, re.S))


def strip(fragment):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def table(html, table_id):
    match = re.search(rf'id="{table_id}".*?<tbody>(.*?)</tbody>', html, re.S)
    if not match:
        return None
    rows = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", match.group(1), re.S):
        head = strip(re.search(r"<th[^>]*>(.*?)</th>", row, re.S).group(1))
        head = head.replace("Small sample", "").strip()
        rows[head] = [strip(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
    return rows


def payload_of(html):
    m = re.search(r'id="dashboard-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


# ---------- Compare one filter combination ----------


def compare(client, rows, f, collected):
    query = "&".join(f"{k}={v}" for k, v in f.items() if v)
    tag = query or "(no filters)"
    response = client.get("/analytics?" + query)
    check(response.status_code == 200, f"{tag}: page status {response.status_code}")
    html = response.get_data(as_text=True)
    trades, _ = independent(rows, f)
    s = summary(trades)
    k = kpis(html)

    expected = {"trades": str(s["trades"])}
    if s["trades"]:
        expected.update(
            {
                "wins": str(s["wins"]),
                "losses": str(s["losses"]),
                "breakevens": str(s["breakevens"]),
                "win_rate": fmt_rate(s["wins"], s["trades"]),
                "average_r": fmt_r(s["mean_r"]),
                "expectancy_r": fmt_r(s["mean_r"]),
                "profit_factor_r": fmt_ratio(s["pf"]),
                "total_r": fmt_r(s["total_r"]),
                "max_drawdown_r": f"{q(s['max_dd'], '0.01')}R",
                "longest_win_streak": f"{s['win_streak']} trade" + ("" if s["win_streak"] == 1 else "s"),
                "longest_loss_streak": f"{s['loss_streak']} trade" + ("" if s["loss_streak"] == 1 else "s"),
            }
        )
    else:
        expected.update(
            dict.fromkeys(
                (
                    "wins",
                    "win_rate",
                    "average_r",
                    "expectancy_r",
                    "profit_factor_r",
                    "total_r",
                    "max_drawdown_r",
                ),
                "n/a",
            )
        )
    for key, value in expected.items():
        check(k.get(key) == value, f"{tag}: KPI {key} page={k.get(key)!r} independent={value!r}")

    # Money per currency, never combined.
    money = defaultdict(Decimal)
    for t in trades:
        money[t["cur"]] += t["net"]
    for cur, net in money.items():
        check(f"{q(net, '0.01')} {cur}" in html, f"{tag}: money {q(net, '0.01')} {cur} missing")

    api = {
        name: client.get(f"/api/v1/analytics/{name}?{query}").get_json()
        for name in ("summary", "instruments", "strategies", "sessions", "weekday", "monthly", "curve")
    }
    o = api["summary"]["data"]["overall"]
    check(o["trades"] == s["trades"], f"{tag}: API trades")
    check(abs(Decimal(o["total_r"]) - s["total_r"]) < TOL, f"{tag}: API total_r")

    if not s["trades"]:
        check(payload_of(html) is None, f"{tag}: payload present with no results")
        return

    payload = payload_of(html)
    collected.append({"tag": tag, "payload": payload})

    # Curve: same trades, same order, same exact cumulative values.
    cum = Decimal(0)
    points = payload["curve"][1:]
    check([p["trade_id"] for p in points] == [t["id"] for t in trades], f"{tag}: curve order")
    check(
        [p["trade_id"] for p in api["curve"]["data"]] == [t["id"] for t in trades], f"{tag}: API curve order"
    )
    # strict=False: a length mismatch is already reported by the order check above.
    for p, t in zip(points, trades, strict=False):
        with localcontext() as ctx:  # same precision as independent()
            ctx.prec = 80
            cum += t["r"]
        check(abs(Decimal(p["cumulative_r"]) - cum) < TOL, f"{tag}: curve #{t['id']} cumulative")
        check(p["cumulative_text"] == fmt_r(cum), f"{tag}: curve #{t['id']} text")
    check(abs(Decimal(payload["max_drawdown"]["amount"]) - s["max_dd"]) < TOL, f"{tag}: payload max drawdown")

    # Groups: page table, payload and API all equal the independent figures.
    specs = [
        ("instruments", "table-instruments", lambda t: t["symbol"], lambda key: key, "instruments"),
        (
            "strategies",
            "table-strategies",
            lambda t: t["strategy"] or "Unspecified",
            lambda key: key,
            "strategies",
        ),
        ("sessions", "table-sessions", lambda t: t["session"], lambda key: SESSION_LABELS[key], "sessions"),
    ]
    for name, table_id, key, label, api_name in specs:
        truth = {label(g): v for g, v in grouped(trades, key).items()}
        page_rows = table(html, table_id)
        check(
            set(page_rows) == set(truth), f"{tag}: {name} table rows {sorted(page_rows)} vs {sorted(truth)}"
        )
        for group, v in truth.items():
            cells = page_rows.get(group)
            if not cells:
                continue
            check(cells[0] == str(v["trades"]), f"{tag}: {name}/{group} trades")
            check(
                cells[1] == f"{v['wins']} / {v['losses']} / {v['breakevens']}", f"{tag}: {name}/{group} w/l/b"
            )
            check(cells[2] == fmt_rate(v["wins"], v["trades"]), f"{tag}: {name}/{group} win rate")
            check(
                cells[5] == fmt_r(v["total_r"]),
                f"{tag}: {name}/{group} total {cells[5]} vs {fmt_r(v['total_r'])}",
            )
        pay = {p["label"]: p for p in payload[name]}
        check(set(pay) == set(truth), f"{tag}: {name} payload labels")
        for group, v in truth.items():
            if group in pay:
                check(
                    abs(Decimal(pay[group]["total_r"]) - v["total_r"]) < TOL, f"{tag}: {name}/{group} payload"
                )
        api_rows = {
            (
                SESSION_LABELS[g["group"]]
                if name == "sessions" and g["group"] in SESSION_LABELS
                else g["group"]
            ): g
            for g in api[api_name]["data"]
        }
        check(set(api_rows) == set(truth), f"{tag}: {name} API groups {sorted(api_rows)}")
        # Order: best total R first (instruments/strategies).
        if name != "sessions":
            order = [p["label"] for p in payload[name]]
            check(order == sorted(order, key=lambda g: (-truth[g]["total_r"], g)), f"{tag}: {name} order")

    # Months: every month from first to last, gaps kept.
    month_truth = grouped(trades, lambda t: t["when"][:7])
    first, last = min(month_truth), max(month_truth)
    expected_keys, y, m = [], int(first[:4]), int(first[5:])
    while f"{y:04d}-{m:02d}" <= last:
        expected_keys.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    check([p["key"] for p in payload["monthly"]] == expected_keys, f"{tag}: month keys")
    for p in payload["monthly"]:
        v = month_truth.get(p["key"])
        if v is None:
            check(p["total_r"] is None, f"{tag}: empty month {p['key']} not null")
        else:
            check(abs(Decimal(p["total_r"]) - v["total_r"]) < TOL, f"{tag}: month {p['key']}")
    month_table = table(html, "table-monthly")
    check(len(month_table) == len(expected_keys), f"{tag}: month table rows")

    # Weekdays, UTC.
    def weekday(t):
        return WEEKDAYS[datetime.strptime(t["when"], "%Y-%m-%dT%H:%M:%SZ").weekday()]

    day_truth = grouped(trades, weekday)
    check([p["key"] for p in payload["weekday"]] == WEEKDAYS, f"{tag}: weekday order")
    day_table = table(html, "table-weekday")
    for p in payload["weekday"]:
        v = day_truth.get(p["key"])
        cells = day_table[p["key"]]
        if v is None:
            check(p["total_r"] is None and cells == ["No closed trades"], f"{tag}: {p['key']} empty")
        else:
            check(abs(Decimal(p["total_r"]) - v["total_r"]) < TOL, f"{tag}: weekday {p['key']}")
            check(cells[5] == fmt_r(v["total_r"]), f"{tag}: weekday table {p['key']}")


NODE_SCRIPT = r"""
const fs = require("fs");
const charts = require(process.argv[2]);
const items = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const theme = {text: "#fff", muted: "#aaa", grid: "#333", zero: "#999", accent: "#00f",
               positive: "#0f0", negative: "#f00", font: "sans-serif"};
const out = items.map(({tag, payload}) => {
  const c = charts.buildConfigs(payload, theme);
  const res = {tag};
  for (const name of Object.keys(c)) {
    const cb = c[name].options.plugins.tooltip.callbacks;
    const n = c[name].data.datasets[0].data.length;
    res[name] = {labels: c[name].data.labels || null, data: c[name].data.datasets[0].data,
                 tooltips: Array.from({length: n}, (_, i) => cb.label({dataIndex: i})),
                 titles: Array.from({length: n}, (_, i) => cb.title ? cb.title([{dataIndex: i}]) : null)};
  }
  return res;
});
fs.writeFileSync(process.argv[4], JSON.stringify(out));
"""


def check_tooltips(collected, node):
    work = Path(tempfile.mkdtemp())
    (work / "run.js").write_text(NODE_SCRIPT)
    (work / "in.json").write_text(json.dumps(collected))
    subprocess.run(
        [
            node,
            str(work / "run.js"),
            str(PROJECT / "tradeledger/static/js/analytics.js"),
            str(work / "in.json"),
            str(work / "out.json"),
        ],
        check=True,
    )
    results = json.loads((work / "out.json").read_text())
    for item, res in zip(collected, results, strict=True):
        tag, payload = item["tag"], item["payload"]
        # Curve: every point's tooltip quotes the server's exact display text,
        # and the plotted y equals the exact value converted once.
        for i, p in enumerate(payload["curve"]):
            lines = res["curve"]["tooltips"][i]
            check(res["curve"]["data"][i]["y"] == float(Decimal(p["cumulative_r"])), f"{tag}: curve y {i}")
            if not p.get("start"):
                check(f"Cumulative R: {p['cumulative_text']}" in lines, f"{tag}: curve tooltip {i}")
                check(f"Trade R: {p['realised_text']}" in lines, f"{tag}: trade R tooltip {i}")
                check(
                    ("Exit time not recorded: placed at entry time" in lines) == (not p["close_time_known"]),
                    f"{tag}: legacy note {i}",
                )
            dd = res["drawdown"]["tooltips"][i][0]
            check(
                dd
                == (
                    f"Drawdown: {p['drawdown_text']} below the running peak"
                    if Decimal(p["underwater_r"]) != 0
                    else dd
                ),
                f"{tag}: drawdown tooltip {i}",
            )
        # Bars: tooltip text equals payload text; null periods stay null.
        for name in ("monthly", "weekday", "instruments", "strategies", "sessions"):
            for i, row in enumerate(payload[name]):
                lines = res[name]["tooltips"][i]
                if row["total_r"] is None:
                    check(
                        res[name]["data"][i] is None and lines == ["No closed trades"],
                        f"{tag}: {name} gap {i}",
                    )
                else:
                    check(lines[0] == f"Total R: {row['total_r_text']}", f"{tag}: {name} tooltip {i}")
                    check(
                        f"Closed trades: {row['trades']} (won {row['wins']}, lost {row['losses']})" in lines,
                        f"{tag}: {name} count tooltip {i}",
                    )
                    check(res[name]["data"][i] == float(Decimal(row["total_r"])), f"{tag}: {name} value {i}")
    return len(results)


def sign_in_as_audited(app, client):
    """The same session a real sign-in creates, plus an API token."""
    import time

    now = int(time.time())
    with app.app_context():  # Phase 8.5: a real sign-in also writes a session row
        key, _ = sessions.create_session(
            get_db(),
            user_id=AUDITED,
            now=now,
            max_age_seconds=app.config["SESSION_MAX_AGE_SECONDS"],
            user_agent="audit",
        )
    with client.session_transaction() as session:
        session.update({"sid": key, "uid": AUDITED, "sv": 1, "auth_at": now, "seen_at": now})
    with app.app_context():
        token, _ = api_tokens.create_token(
            get_db(), user_id=AUDITED, name="audit", lifetime_days=1, max_active=10
        )
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"


def main():
    node = sys.argv[sys.argv.index("--node") + 1] if "--node" in sys.argv else shutil.which("node")
    if not node:
        sys.exit("Node.js is needed to run analytics.js; pass --node /path/to/node")
    if "--pg-bridge" in sys.argv:
        sys.path.insert(0, str(PROJECT / "tests"))
        import pg_libpq_bridge

        register_postgres_driver(pg_libpq_bridge)
    target = sys.argv[sys.argv.index("--database-url") + 1] if "--database-url" in sys.argv else None
    target = target or str(Path(tempfile.mkdtemp()) / "qa.db")
    app = build_database(target)
    rows = raw_rows(app)
    client = app.test_client()
    sign_in_as_audited(app, client)
    with app.app_context():
        instrument_ids = [
            r[0]
            for r in get_db().execute("SELECT id FROM instruments WHERE user_id = ? ORDER BY id", (AUDITED,))
        ]
        decoy_ids = [
            r[0] for r in get_db().execute("SELECT id FROM instruments WHERE user_id <> ?", (AUDITED,))
        ]
    # A decoy instrument id is someone else's: the page and the API must
    # treat it exactly like an id that does not exist.
    for decoy_id in decoy_ids[:2]:
        page = client.get(f"/analytics?instrument_id={decoy_id}")
        check(page.status_code == 400, f"decoy instrument {decoy_id}: page status {page.status_code}")
        api = client.get(f"/api/v1/analytics/summary?instrument_id={decoy_id}")
        check(api.status_code == 400, f"decoy instrument {decoy_id}: API status {api.status_code}")
    strategies = [None, "Breakout", "Pullback", "Scalp", "Reversal"]
    sessions = [None, "ASIA", "LONDON", "NEW_YORK", "OTHER"]
    dates = [(None, None), ("2026-01-01", "2026-02-28"), ("2025-12-15", None), ("2026-03-02", "2026-03-02")]
    collected = []
    combos = 0
    for inst, strat, sess, (start, end) in itertools.product(
        [None, *instrument_ids], strategies, sessions, dates
    ):
        f = {"instrument_id": inst, "strategy": strat, "session": sess, "start": start, "end": end}
        compare(client, rows, f, collected)
        combos += 1
    charted = check_tooltips(collected, node)
    closed = sum(1 for r in rows if r["status"] == "CLOSED")
    legacy = sum(1 for r in rows if r["status"] == "CLOSED" and not r["closed_at"])
    print(f"trades in database: {len(rows)} ({closed} closed, {legacy} with no exit time)")
    print(f"filter combinations: {combos}; with results (charts checked in Node): {charted}")
    print(f"individual comparisons: {checks}; disagreements: {len(problems)}")
    for p in problems[:30]:
        print("  -", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
