r"""Triangulate the stream ledger against our own tape: every entry price must have traded around its time, every exit price
(entry +/- the P&L step) around its time. OCR misreads (3<->8, 1<->7, a dropped digit) are repaired when exactly one
digit-edit candidate traded; a flat seen after a capture gap is marked as the net of unobserved trades, not one exit.

check_trades(rows, gaps) -> rows with entry_ok / exit_ok / check columns added (rows are stream_trades.py dicts).
gaps: {date: [(t0, t1), ...]} spans (ET datetimes) longer than 3 min with no panel read, from the state.csv files."""
from __future__ import annotations

import csv
import itertools
from datetime import datetime, timedelta
from pathlib import Path

import tape_seconds as tape

POINT_USD = 20.0
SUBS = {"3": "8", "8": "3", "1": "7", "7": "1", "0": "8", "6": "8", "5": "6", "9": "0", "2": "7"}


def _candidates(price: float) -> list[float]:
    """Prices one OCR digit-confusion away (ticks of 0.25 kept)."""
    s = f"{price:.2f}"
    out = set()
    digits = [i for i, ch in enumerate(s) if ch.isdigit()]
    for i in digits:
        ch = s[i]
        if ch in SUBS:
            cand = s[:i] + SUBS[ch] + s[i + 1:]
            try:
                v = float(cand)
                if abs(v * 4 - round(v * 4)) < 1e-6 and 1000 < v < 100000:
                    out.add(round(v, 2))
            except ValueError:
                pass
    return sorted(out - {price})


def _money_candidates(usd: float) -> list[float]:
    """Realized-P&L readings one digit-edit away, incl. a dropped leading digit ($12,735 read as $1,735)."""
    s = f"{abs(usd):.0f}"
    out = set()
    for i, ch in enumerate(s):
        if ch in SUBS:
            try:
                out.add(float(s[:i] + SUBS[ch] + s[i + 1:]))
            except ValueError:
                pass
    for d in "123456789":  # a dropped leading digit
        out.add(float(d + s))
    sign = -1 if usd < 0 else 1
    return sorted({sign * v for v in out} - {usd})


def _parse_et(date: str, hms: str) -> datetime | None:
    if not hms:
        return None
    try:
        return datetime.strptime(f"{date} {hms}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(f"{date} {hms}", "%Y-%m-%d %H:%M")
        except ValueError:
            return None


def capture_gaps(state_files: list[Path], min_gap_s: int = 180) -> dict[str, list[tuple[datetime, datetime]]]:
    """Spans with no panel read (state.csv rows are every 10 s while the capture runs), per ET date."""
    gaps: dict[str, list[tuple[datetime, datetime]]] = {}
    for p in state_files:
        prev = None
        for r in csv.DictReader(open(p, encoding="utf-8")):
            try:
                t = datetime.strptime(r["recv_utc"][:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, KeyError):
                continue
            if prev and (t - prev).total_seconds() >= min_gap_s:
                et0, et1 = prev - timedelta(hours=5), t - timedelta(hours=5)  # EST; the ledger uses the same offset rule
                gaps.setdefault(et0.strftime("%Y-%m-%d"), []).append((et0, et1))
            prev = t
    return gaps


def calibrate_video(rows: list[dict]) -> float:
    """A downloaded stream's clock can be off by its encoder delay: find the shift (s) that puts the most entry prices
    onto the tape within +/-20 s of their frame time. 0 when there is nothing to calibrate on."""
    cands = []
    for r in rows:
        t = _parse_et(r["date"], r.get("entry_et"))
        if t and r.get("entry") not in ("", None) and not r.get("first_event"):
            cands.append((t, float(r["entry"])))
    if len(cands) < 2:
        return 0.0
    # histogram of "offset that would put this entry's price on the tape": for every entry, every second within +/-3 h
    # where its price traded votes for (that second - frame time); the offset with the most entries (within 20 s) wins.
    # A start taken from the title (scheduled time) can be an hour off, which the old +/-30 min search missed.
    import numpy as np
    span = 3 * 3600
    votes: dict[int, set[int]] = {}
    step, tol = 5, 20
    for k, (t, p) in enumerate(cands):
        s = tape.seconds_around(t, span, span)
        if s is not None and len(s):
            hit = s[(s["lo"] <= p + 0.5) & (s["hi"] >= p - 0.5)]
            for ts in hit.index:
                off = int(round((ts - t).total_seconds() / 5.0)) * 5
                votes.setdefault(off, set()).add(k)
            continue
        # no tape that day (before 2026-10-22): vote per minute bar whose range holds the price
        bars = tape.minute_bars(t.strftime("%Y-%m-%d"))
        if bars is None or not len(bars):
            continue
        step, tol = 60, 120
        w = bars.loc[t - timedelta(seconds=span): t + timedelta(seconds=span)]
        hit = w[(w["low"] - 0.5 <= p) & (w["high"] + 0.5 >= p)]
        for ts in hit.index:  # close-stamped bar: its minute is (ts-60s, ts]
            off = int(round((ts - timedelta(seconds=30) - t).total_seconds() / 60.0)) * 60
            votes.setdefault(off, set()).add(k)
    if not votes:
        return 0.0
    # smooth: an entry counts for an offset if it voted within `tol` seconds of it
    best, best_n = 0.0, -1
    for off in sorted(votes):
        n = len(set().union(*(votes.get(o, set()) for o in range(off - tol, off + tol + 1, step))))
        if n > best_n or (n == best_n and abs(off) < abs(best)):
            best, best_n = off, n
    return float(best) if best_n >= max(2, len(cands) // 2) else 0.0


def check_trades(rows: list[dict], gaps: dict[str, list[tuple[datetime, datetime]]]) -> list[dict]:
    offsets: dict[str, float] = {}
    for vid in {r.get("video") for r in rows if r.get("video") not in (None, "live", "screenshot")}:
        offsets[vid] = calibrate_video([r for r in rows if r.get("video") == vid])
    for r in rows:
        try:
            _check_one(r, gaps, offsets.get(r.get("video"), 0.0))
        except Exception as e:  # noqa: BLE001
            r["check"] = f"check failed: {e}"
    return rows


def _snap_entry_bar(r: dict, t_in: datetime, entry: float) -> None:
    """His fills land on a bar's OPEN (market order on the previous bar's close). The frame time is only approximate
    (video clock, 5-s steps), so pick, within +/-5 min, the bar whose open is nearest the fill; the SIGNAL bar is the one
    before it (close-stamped labels: the bar that closed at the fill). Keeps the label when nothing is within 3 ticks."""
    bars = tape.minute_bars(r["date"])
    if bars is None or not len(bars):
        return
    t0 = t_in.replace(second=0)
    best = None
    for k in range(-5, 6):
        tb = t0 + timedelta(minutes=k)
        if tb in bars.index:
            d = abs(float(bars.loc[tb, "open"]) - entry) / 0.25
            if best is None or d < best[0] or (d == best[0] and abs(k) < abs(best[1])):
                best = (d, k, tb)
    if best and best[0] <= 3:
        sig = best[2] - timedelta(minutes=1)
        new = sig.strftime("%H:%M")
        if new != r.get("entry_bar"):
            r["check"] = (r.get("check", "") + f"; signal bar snapped {r.get('entry_bar')} -> {new} (fill = {best[2]:%H:%M} open, {best[0]:.0f} ticks)").strip("; ")
            r["entry_bar"] = new
            r["entry_et"] = best[2].strftime("%H:%M:%S")


def _shift(r: dict, key: str, off: float) -> None:
    t = _parse_et(r["date"], r.get(key))
    if t and off:
        r[key] = (t + timedelta(seconds=off)).strftime("%H:%M:%S")


def _check_one(r: dict, gaps: dict, off: float) -> None:
    for k in ("entry_ok", "exit_ok", "check"):
        r.setdefault(k, "")
    notes = []
    if off:
        _shift(r, "entry_et", off)
        _shift(r, "exit_et", off)
        t = _parse_et(r["date"], r.get("entry_et"))
        if t:
            r["entry_bar"] = t.strftime("%H:%M")
        notes.append(f"video clock shifted {off:+.0f} s to fit the tape")
    if r.get("first_event"):
        r["entry_ok"] = "open at video start"
        notes.append("position already open on the first frame; entry time unknown")
        r["check"] = "; ".join(notes)
        return
    if True:
        t_in = _parse_et(r["date"], r.get("entry_et") or (r.get("entry_bar") and r["entry_bar"] + ":00"))
        entry = float(r["entry"]) if r.get("entry") not in ("", None) else None
        if t_in and entry is not None:
            ok, rng = tape.traded_near(entry, t_in, 60, 120)
            if ok:
                r["entry_ok"] = "yes"
                _snap_entry_bar(r, t_in, entry)
            else:
                fixes = [c for c in _candidates(entry) if tape.traded_near(c, t_in, 60, 120)[0]]
                if len(fixes) == 1:
                    notes.append(f"entry {entry} never traded ({rng}); read as {fixes[0]} (one-digit OCR fix)")
                    r["entry"] = fixes[0]
                    entry = fixes[0]
                    r["entry_ok"] = "fixed"
                else:
                    r["entry_ok"] = "NO"
                    notes.append(f"entry {entry} never traded near {t_in:%H:%M:%S} ({rng})")
        t_out = _parse_et(r["date"], r.get("exit_et"))
        usd = float(r["usd"]) if r.get("usd") not in ("", None) else None
        side = 1 if r.get("side") == "long" else -1 if r.get("side") == "short" else 0
        if t_out and usd is not None and entry is not None and side:
            in_gap = any(g0 <= t_out <= g1 + timedelta(seconds=60) and g0 >= (t_in or g0) for g0, g1 in gaps.get(r["date"], []))
            exit_price = round(entry + side * usd / POINT_USD, 2)
            ok, rng = tape.traded_near(exit_price, t_out, 120, 60)
            if ok and not in_gap:
                r["exit_ok"] = "yes"
            elif in_gap:
                r["exit_ok"] = "gap"
                r["exit_kind"] = "gap"
                notes.append(f"capture gap before this flat: ${usd:+,.0f} is the net of unobserved trades, not one exit")
                r["exit"] = ""
            else:
                fixes = []
                for cand in _money_candidates(usd):
                    ep = round(entry + side * cand / POINT_USD, 2)
                    if tape.traded_near(ep, t_out, 120, 60)[0]:
                        fixes.append((cand, ep))
                if len(fixes) == 1:
                    cand, ep = fixes[0]
                    notes.append(f"exit {exit_price} never traded ({rng}); P&L read as ${cand:+,.0f} -> exit {ep} (OCR fix)")
                    r["usd"], r["pts"], r["exit"] = cand, round(cand / POINT_USD, 2), ep
                    from stream_trades import exit_kind_of
                    r["exit_kind"] = exit_kind_of(cand / POINT_USD)
                    r["exit_ok"] = "fixed"
                else:
                    r["exit_ok"] = "NO"
                    notes.append(f"exit {exit_price} never traded near {t_out:%H:%M:%S} ({rng})")
        r["check"] = "; ".join(notes)
