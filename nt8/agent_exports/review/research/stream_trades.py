from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = Path(r"D:\stream_capture\DaCaptainTrader_")
POINT_USD = 20.0
TARGET_PTS = 50.0
TARGETS_PTS = (2.5, 5.0, 10.0, 15.0, 25.0, 50.0, 75.0, 125.0)
VIDEO_INFO: list[dict] = []


def exit_kind_of(pts: float) -> str:
    for t in TARGETS_PTS:
        if abs(pts - t) <= 0.3:
            return f"target{t:g}"
    return "stop" if pts < 0 else "other"


def to_et(utc_str: str, lag: float) -> datetime:
    t = datetime.strptime(utc_str[:23], "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc) - timedelta(seconds=lag)
    y = t.year
    mar = datetime(y, 3, 8, 7, tzinfo=timezone.utc)
    while mar.weekday() != 6:
        mar += timedelta(days=1)
    nov = datetime(y, 11, 1, 6, tzinfo=timezone.utc)
    while nov.weekday() != 6:
        nov += timedelta(days=1)
    off = -4 if mar <= t < nov else -5
    return (t + timedelta(hours=off)).replace(tzinfo=None)


def _price_of(text) -> float | None:
    m = re.search(r"(?<!\d)(\d{5})[.,]?(\d{2})(?!\d)", str(text or "").replace(" ", ""))
    return float(f"{m.group(1)}.{m.group(2)}") if m else None


def _money_of(text) -> float | None:
    t = str(text or "").replace(" ", "").replace("o", "0").replace("O", "0")
    m = re.search(r"\(?\$?(-?[\d,]+\.\d{2})\)?", t)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return -abs(v) if "(" in t else v


def _norm_params(text) -> str:
    m = re.search(r"(\d{2,4})\s*/\s*1\s*/\s*-?\s*(\d{1,4})", str(text or ""))
    return f"{m.group(1)}/1/-{m.group(2)}" if m else ""


def _in_pos(row: dict) -> bool:
    return (row.get("side") or "flat") in ("long", "short", "?")


def _elect_row(state_csv: Path) -> dict:
    info = {"row": None, "copy": "", "agree": 0.0, "samples": 0, "rows": 0}
    if not state_csv.exists():
        return info
    samples = []
    with open(state_csv, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("state") not in ("pos", "flat") or not r.get("grid_positions"):
                continue
            items = re.findall(r"(.+?)=(flat|long|short|\?)(\d+)(?:\s|$)", r["grid_positions"])
            if items:
                samples.append((r["state"] == "pos", items))
    if len(samples) < 30:
        return info
    modal = Counter(len(s[1]) for s in samples).most_common(1)[0][0]
    use = [s for s in samples if len(s[1]) == modal]
    agree, labels = Counter(), defaultdict(Counter)
    for pos, items in use:
        for i, (params, side, _) in enumerate(items):
            agree[i] += int((side != "flat") == pos)
            labels[i][_norm_params(params)] += 1
    ranked = sorted(range(modal), key=lambda i: -agree[i])
    best = ranked[0]
    ratio = agree[best] / len(use)
    second = agree[ranked[1]] / len(use) if modal > 1 else 0.0
    info.update(agree=round(ratio, 3), samples=len(use), rows=modal)
    if ratio >= 0.85 and ratio - second >= 0.05:
        lab = [p for p, _ in labels[best].most_common() if p]
        info.update(row=best, copy=lab[0] if lab else f"row{best}")
    return info


def _day_reset_between(t0_utc: str, t1_utc: str) -> bool:
    a, b = to_et(t0_utc, 0.0) - timedelta(hours=18), to_et(t1_utc, 0.0) - timedelta(hours=18)
    return a.date() != b.date()


def _video_trades(ev_path: Path, lag: float, video: str) -> tuple[list[dict], dict]:
    trades: list[dict] = []
    open_trade = None
    last_realized = None
    last_realized_t = None
    last_from_frame = False
    first_event = True
    readings: list[tuple[int, list[dict]]] = []
    exits: list[int] = []
    try:
        import frame_pass
        reread = frame_pass.rereads(ev_path.parent)
    except Exception:
        reread = {}

    def read_value(e: dict, key: str) -> tuple[float | None, bool]:
        v = e.get(key)
        if v is not None:
            return v, False
        if e.get("frame") and reread.get(e["frame"]) is not None:
            return reread[e["frame"]], True
        return None, False

    for seq, line in enumerate(ev_path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        e = json.loads(line)
        k = e.get("kind")
        rows = e.get("rows") if k == "grid_change" else e.get("grid")
        if rows:
            readings.append((seq, rows))
        if k in ("flat", "exit", "realized_change", "grid_change"):
            first_event = False
        if k in ("flat", "realized_change"):
            v, from_frame = read_value(e, "after" if k == "realized_change" else "realized")
            if v is not None:
                last_realized, last_realized_t, last_from_frame = v, e["recv_utc"], from_frame
        if k == "entry":
            avg = e.get("avg")
            if open_trade and open_trade["entry"] == avg:
                if open_trade["side"] == "?" and e.get("side") in ("long", "short"):
                    open_trade["side"] = e["side"]
                continue
            if open_trade and open_trade["entry"] != avg:
                open_trade["note"] = "exit not seen"
                trades.append(open_trade)
            side = e.get("side") or "?"
            if side == "?" and e.get("grid"):
                sides = {r.get("side") for r in e["grid"] if r.get("params", "").startswith("200")}
                side = sides.pop() if len(sides) == 1 and sides != {"flat"} else "?"
            t = to_et(e["recv_utc"], lag)
            open_trade = {"date": t.strftime("%Y-%m-%d"), "entry_et": t.strftime("%H:%M:%S"),
                          "entry_bar": t.replace(second=0).strftime("%H:%M"), "side": side, "entry": avg,
                          "exit_et": "", "exit": "", "exit_kind": "", "pts": "", "usd": "", "usd_grid": "", "copy": "",
                          "source": "capture", "note": "", "video": video, "first_event": first_event, "_seq_entry": seq}
            first_event = False
        elif k == "exit" and open_trade:
            before, after = e.get("realized_before"), e.get("realized")
            from_frame = False
            if after is None:
                after, from_frame = read_value(e, "realized")
            if before is None:
                before, from_frame = last_realized, from_frame or last_from_frame
                if before is not None and last_realized_t and _day_reset_between(last_realized_t, e["recv_utc"]):
                    before = 0.0
                    open_trade["note"] = "P&L = the reading after the day reset"
            if after is not None:
                last_realized, last_realized_t, last_from_frame = after, e["recv_utc"], from_frame
            t = to_et(e["recv_utc"], lag)
            open_trade["exit_et"] = t.strftime("%H:%M:%S")
            if before is not None and after is not None:
                usd = after - before
                pts = usd / POINT_USD
                sign = 1 if open_trade["side"] == "long" else -1 if open_trade["side"] == "short" else 0
                open_trade["usd"] = round(usd, 2)
                open_trade["pts"] = round(pts, 2)
                open_trade["exit_kind"] = exit_kind_of(pts)
                open_trade["exit_src"] = "frame" if from_frame else "panel"
                if sign and open_trade["entry"] is not None:
                    open_trade["exit"] = round(open_trade["entry"] + sign * pts, 2)
            else:
                open_trade["note"] = "realized not read"
            open_trade["_seq_exit"] = seq
            exits.append(seq)
            trades.append(open_trade)
            open_trade = None
    if open_trade:
        open_trade["note"] = "still open at end of file"
        trades.append(open_trade)

    info = _elect_row(ev_path.with_name("state.csv"))
    info.update(video=video, readings=len(readings), trades=len(trades), check="")
    row = info["row"]
    pairs: list[tuple[dict, float]] = []
    for t in trades if row is not None else []:
        if "_seq_exit" not in t:
            continue
        s_in, s_out = t["_seq_entry"], t["_seq_exit"]
        s_next = next((s for s in exits if s > s_out), 10 ** 9)
        before = after = None
        for seq, rows in readings:
            if len(rows) != info["rows"]:
                continue
            r = rows[row]
            if s_in <= seq <= s_out and _in_pos(r):
                before_match = before is not None and _price_of(before.get("avg")) == t["entry"]
                if before is None or _price_of(r.get("avg")) == t["entry"] or not before_match:
                    before = r
            elif s_out <= seq < s_next and not _in_pos(r):
                after = r
        if before is None or after is None:
            continue
        rb, ra = _money_of(before.get("realized")), _money_of(after.get("realized"))
        if rb is not None and ra is not None:
            pairs.append((t, round(ra - rb, 2)))
    both = [(t, g) for t, g in pairs if t["usd"] != ""]
    bad = sum(abs(g - float(t["usd"])) > 1 for t, g in both)
    if both and bad * 2 > len(both):
        info.update(row=None, copy="", check=f"grid contradicts the panel {bad}/{len(both)} - row dropped")
        pairs = []
    elif both:
        info["check"] = f"{len(both) - bad}/{len(both)} P&L steps match"
    for t in trades:
        t["copy"] = info["copy"]
    for t, g in pairs:
        t["usd_grid"] = g
        if t["usd"] == "":
            sign = 1 if t["side"] == "long" else -1 if t["side"] == "short" else 0
            t["usd"], t["pts"] = g, round(g / POINT_USD, 2)
            t["exit_kind"] = exit_kind_of(g / POINT_USD)
            if sign and t["entry"] is not None:
                t["exit"] = round(t["entry"] + sign * g / POINT_USD, 2)
            t["note"] = "; ".join(x for x in (t["note"], "P&L from the grid row (panel P&L not read)") if x)
        elif abs(g - float(t["usd"])) > 1:
            t["note"] = "; ".join(x for x in (t["note"], f"grid row says ${g:+,.0f} vs panel ${float(t['usd']):+,.0f}") if x)
    return trades, info


def captured_trades(lag: float) -> list[dict]:
    trades: list[dict] = []
    VIDEO_INFO.clear()
    for day_dir in sorted(BASE.glob("20??-??-??")) + sorted((BASE / "vod").glob("*")):
        ev_path = day_dir / "events.jsonl"
        if not ev_path.exists():
            continue
        is_vod = day_dir.parent.name == "vod"
        t, info = _video_trades(ev_path, 0.0 if is_vod else lag, day_dir.name if is_vod else "live")
        trades += t
        VIDEO_INFO.append(info)
    return trades


def manual_trades() -> list[dict]:
    p = BASE / "manual_trades.csv"
    if not p.exists():
        return []
    rows = []
    for r in csv.DictReader(open(p, encoding="utf-8")):
        r.setdefault("source", "screenshot")
        for k in ("entry", "exit"):
            r[k] = float(r[k]) if r.get(k) else ""
        if r["entry"] != "" and r["exit"] != "" and r.get("side") in ("long", "short"):
            sign = 1 if r["side"] == "long" else -1
            r["pts"] = round(sign * (r["exit"] - r["entry"]), 2)
            r["usd"] = round(r["pts"] * POINT_USD, 2)
            r["exit_kind"] = exit_kind_of(r["pts"])
        rows.append({"date": r["date"], "entry_et": r.get("entry_et", ""), "entry_bar": r.get("entry_bar", ""), "side": r["side"],
                     "entry": r["entry"], "exit_et": r.get("exit_et", ""), "exit": r["exit"], "exit_kind": r.get("exit_kind", ""),
                     "pts": r.get("pts", ""), "usd": r.get("usd", ""), "usd_grid": "", "copy": "", "source": r["source"],
                     "note": r.get("note", ""), "video": "screenshot", "first_event": False})
    return rows


def build(lag: float = 22.0) -> list[dict]:
    rows = manual_trades() + captured_trades(lag)
    seen = set()
    out = []
    for r in rows:
        key = (r["date"], r["side"], r["entry"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    out.sort(key=lambda r: (r["date"], r["entry_bar"] or r["entry_et"]))
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        import ledger_check
        state_files = sorted(BASE.glob("20??-??-??/state.csv")) + sorted((BASE / "vod").glob("*/state.csv"))
        out = ledger_check.check_trades(out, ledger_check.capture_gaps(state_files))
    except Exception as e:
        print("tape check skipped:", e)
    best: dict[tuple, dict] = {}
    for r in out:
        key = (r["date"], r["side"], str(r["entry"]))
        score = (r.get("usd") not in ("", None), bool(r.get("exit_et")))
        if key not in best or score > best[key][0]:
            best[key] = (score, r)
    keep = {id(v[1]) for v in best.values()}
    out = [r for r in out if id(r) in keep]
    for r in out:
        if r.get("exit_src") in ("panel", "frame") and r.get("usd") not in ("", None) and r.get("exit_kind") != "gap" \
                and r.get("exit_ok") not in ("yes", "fixed"):
            r["note"] = "; ".join(x for x in (r.get("note", ""), f"{r['exit_src']} P&L ${float(r['usd']):+,.0f} not this trade's "
                                  f"(its exit price never traded) - tape instead") if x)
            r["usd"] = r["pts"] = r["exit"] = r["exit_kind"] = ""
            r["exit_src"] = ""
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lag", type=float, default=22.0)
    args = ap.parse_args()
    out = build(args.lag)
    try:
        import exit_pass
        out = exit_pass.fill(out)
    except Exception as e:
        print("exit pass skipped:", e)
    cols = ["date", "entry_bar", "entry_et", "side", "entry", "exit_et", "exit", "exit_kind", "pts", "usd", "usd_grid", "copy",
            "source", "note", "video", "entry_ok", "exit_ok", "check", "exit_src"]
    for r in out:
        for k in ("first_event", "_seq_entry", "_seq_exit"):
            r.pop(k, None)
        r.setdefault("usd_grid", "")
        r.setdefault("copy", "")
        r.setdefault("exit_src", "")
    with open(BASE / "trades.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    print(f"{len(out)} trades -> {BASE / 'trades.csv'}")
    print(f"{'date':<10} {'bar':<5} {'side':<5} {'entry':>9} {'exit':>9} {'kind':<6} {'pts':>6} {'usd':>7}  source")
    for r in out:
        print(f"{r['date']:<10} {r['entry_bar']:<5} {r['side']:<5} {r['entry']!s:>9} {r['exit']!s:>9} {r['exit_kind']:<6} {r['pts']!s:>6} {r['usd']!s:>7}  {r['source']} {r['note']}")
    filled = sum("P&L from the grid row" in r["note"] for r in out)
    disagree = sum("grid row says" in r["note"] for r in out)
    confirmed = sum(r["usd_grid"] != "" for r in out) - filled - disagree
    print(f"\ngrid cross-check: confirmed {confirmed}, filled {filled}, disagree {disagree}; the panel's copy per video:")
    for i in VIDEO_INFO:
        print(f"  {i['video']:<12} {i['copy'] or '-':<11} agree {i['agree']:>5.0%} over {i['samples']:>4} samples, "
              f"{i['trades']:>2} trades  {i['check']}")
    done = [r for r in out if r["usd"] != ""]
    if done:
        kinds = Counter(r["exit_kind"] for r in done)
        src = Counter(r["exit_src"] for r in done)
        print(f"closed {len(done)} (P&L from panel {src.get('panel', 0)}, frame {src.get('frame', 0)}, grid {src.get('grid', 0)}, "
              f"tape {src.get('tape', 0)}): "
              f"net ${sum(float(r['usd']) for r in done):,.0f}; exits: " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())))


if __name__ == "__main__":
    main()
