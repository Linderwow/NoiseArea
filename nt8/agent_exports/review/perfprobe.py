r"""
perfprobe.py - one-second samples that show WHERE market data is held up on its way into NinjaTrader (2026-10-27).

Why: on Mon 10-26 and Tue 10-27 NinjaTrader fell 17-22 s behind the market right after the 09:30 open. It took in at most about
2,700 MNQ events a second while the exchange sent up to 3,400 (Fri 10-23 it kept up with 3,900 a second, 0.1 s late). The PC was
2% busy, NinjaTrader used about a third of one core, and a chart order at 09:31:52 was accepted in 31 ms and filled 14 points away
from the chart's 15-second-old price. So neither this PC's CPU nor the order path - but the watchdog's 2-minute samples cannot tell
whether the data reached this PC late (Rithmic's paper-trading data server, the internet line) or arrived on time and waited
inside NinjaTrader.

sample() writes one row a second to agent_exports\records\perf_probe_<date>.csv: how late the newest data is and how many events
NinjaTrader took in (the TapeRecorder tape, read as it grows), bytes and TCP segments arriving at the network card that carries the
internet route, TCP retransmissions, NinjaTrader's CPU and its busiest thread, the whole PC's CPU. The watchdog calls it for the
rest of its 2-minute run from 09:26 to 10:20 ET, 15:56 to 16:04, and whenever data is 2 s late or more.
report() reads a day back for the daily review. For each stretch with data 5 s late or more it says whether one NinjaTrader thread
was maxed out, whether the PC was busy, and - from the tape - whether the network bytes followed the exchange's message rate (the
data was already on this PC: the queue is inside NinjaTrader) or NinjaTrader's intake (the data reached this PC late: the queue is
upstream, at Rithmic or on the internet line). Read-only: never touches NinjaTrader.
md_servers() reads Rithmic's own logs (NinjaTrader 8\trace\Rithmic.<connection>.<date>.000) for the market-data server Rithmic
assigned at each login. 2026-10-27: a login on Sun 10-25 19:14 got 216.57.156.162 - not seen in 96 logins since 09-24, all on
Rithmic's tonpz*.theomne.net machines - and both opens on it ran 18-23 s late (Fri 10-23 on a usual one: 2 s). The report and
the daily review name the server so a reconnect (a new server) can be checked against the next open.

    python perfprobe.py --seconds 20          sample now for 20 s into today's file (prints each row)
    python perfprobe.py --report 2026-10-27   the daily-review lines for that day
    python perfprobe.py --servers             every market-data server Rithmic assigned in the last 60 days, and the one now
"""
import os, re, sys, csv, json, math, time, socket, struct, bisect, ctypes, argparse, subprocess, datetime as dt
from ctypes import wintypes as WT
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
EXPORTS = os.path.normpath(os.path.join(HERE, '..'))
RECORDER = r'D:\nt_recorder'
COLS = ['time_et', 'lag_s', 'lag_max_s', 'intake_ev_s', 'net_in_kb_s', 'tcp_in_seg_s', 'tcp_retrans_s', 'nt_cores',
        'nt_top_thread_pct', 'pc_busy_pct', 'rtt_ms']
PING_S = 10                  # rtt_ms: ping round trip to each Rithmic server NinjaTrader is connected to, every 10 s
                             # (10-27 10:36: 34-46 ms; a throughput ceiling that moves with it = a small TCP window)
LATE_S = 5.0                 # a stretch with data this late is analysed
MAXED_PCT = 90.0             # a thread at this share of one core is maxed out
NOWIN = 0x08000000 if os.name == 'nt' else 0
if os.name == 'nt':          # private DLL objects: argtypes set here never leak into watchdog.py's ctypes calls
    K32 = ctypes.WinDLL('kernel32'); IPH = ctypes.WinDLL('iphlpapi')
    H = ctypes.c_void_p
    K32.OpenProcess.restype = H; K32.OpenThread.restype = H; K32.CreateToolhelp32Snapshot.restype = H
    K32.CloseHandle.argtypes = [H]; K32.GetProcessTimes.argtypes = [H] + [ctypes.c_void_p] * 4
    K32.GetThreadTimes.argtypes = [H] + [ctypes.c_void_p] * 4
    K32.Thread32First.argtypes = [H, ctypes.c_void_p]; K32.Thread32Next.argtypes = [H, ctypes.c_void_p]
INVALID = ctypes.c_void_p(-1).value


class IFROW(ctypes.Structure):           # MIB_IFROW (GetIfEntry): 32-bit counters, plenty for one-second differences
    _fields_ = [('name', ctypes.c_wchar * 256), ('index', WT.DWORD), ('type', WT.DWORD), ('mtu', WT.DWORD), ('speed', WT.DWORD),
                ('physlen', WT.DWORD), ('phys', ctypes.c_ubyte * 8), ('admin', WT.DWORD), ('oper', WT.DWORD),
                ('lastchange', WT.DWORD), ('in_octets', WT.DWORD), ('in_ucast', WT.DWORD), ('in_nucast', WT.DWORD),
                ('in_discards', WT.DWORD), ('in_errors', WT.DWORD), ('in_unknown', WT.DWORD), ('out_octets', WT.DWORD),
                ('out_ucast', WT.DWORD), ('out_nucast', WT.DWORD), ('out_discards', WT.DWORD), ('out_errors', WT.DWORD),
                ('out_qlen', WT.DWORD), ('descrlen', WT.DWORD), ('descr', ctypes.c_ubyte * 256)]


class TE32(ctypes.Structure):            # THREADENTRY32
    _fields_ = [('size', WT.DWORD), ('usage', WT.DWORD), ('tid', WT.DWORD), ('pid', WT.DWORD), ('base', WT.LONG),
                ('delta', WT.LONG), ('flags', WT.DWORD)]


def route_interface():
    """Index of the network interface that carries the internet route; None when unknown."""
    try:
        i = WT.DWORD(); dest = struct.unpack('<I', socket.inet_aton('8.8.8.8'))[0]
        return i.value if IPH.GetBestInterface(WT.DWORD(dest), ctypes.byref(i)) == 0 else None
    except Exception: return None


def net_in(index):
    """Bytes received so far by that interface (a 32-bit counter); None when unreadable."""
    if index is None: return None
    try:
        r = IFROW(); r.index = index
        return r.in_octets if IPH.GetIfEntry(ctypes.byref(r)) == 0 else None
    except Exception: return None


def tcp_counts():
    """(IPv4 segments received, retransmitted, IPv6 received, retransmitted) so far: 32-bit counters."""
    out = []
    for fam in (2, 23):
        s = (WT.DWORD * 15)()
        try: ok_ = IPH.GetTcpStatisticsEx(ctypes.byref(s), fam) == 0
        except Exception: ok_ = False
        out += [s[9], s[11]] if ok_ else [None, None]
    return out


def system_times():
    """(idle, busy) CPU seconds of the whole PC, summed over all cores."""
    t = (ctypes.c_ulonglong * 3)()
    if K32.GetSystemTimes(ctypes.byref(t, 0), ctypes.byref(t, 8), ctypes.byref(t, 16)):
        return t[0] / 1e7, (t[1] + t[2] - t[0]) / 1e7      # kernel time includes idle time
    return None


def nt_pid():
    """Process id of NinjaTrader.exe; None when it is not running."""
    try:
        p = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq NinjaTrader.exe', '/FO', 'CSV', '/NH'], capture_output=True,
                           text=True, creationflags=NOWIN, timeout=20)
        for row in csv.reader(p.stdout.splitlines()):
            if len(row) > 1 and row[0].lower() == 'ninjatrader.exe' and row[1].isdigit(): return int(row[1])
    except Exception: pass
    return None


def tape_path(folder, now):
    """Today's TapeRecorder tape (the file is named by the UTC date); None without a recorder status."""
    try:
        rs = json.load(open(os.path.join(folder, 'TapeRecorder_status.json'), encoding='utf-8'))
        u = now.astimezone(dt.timezone.utc).replace(tzinfo=None)              # this PC runs on US Eastern time
        return os.path.join(rs.get('output_dir') or RECORDER, str(rs.get('instrument') or '').replace(' ', '_'), f"{u:%Y-%m-%d}_tape.csv")
    except Exception: return None


def secs(b):
    """Seconds after midnight from a tape timestamp 'YYYY-MM-DD HH:MM:SS.ffffff' (bytes or str)."""
    return int(b[11:13]) * 3600 + int(b[14:16]) * 60 + float(b[17:26])


class Proc:
    """CPU seconds of one process and of each of its threads (thread handles kept open, the list refreshed every 5 s)."""
    def __init__(self, pid):
        self.pid = pid; self.ph = K32.OpenProcess(0x1000, False, pid) if pid else None; self.th = {}; self.listed = -1e9

    def cpu(self):
        t = (ctypes.c_ulonglong * 4)()
        if self.ph and K32.GetProcessTimes(self.ph, ctypes.byref(t, 0), ctypes.byref(t, 8), ctypes.byref(t, 16), ctypes.byref(t, 24)):
            return (t[2] + t[3]) / 1e7
        return None

    def _list(self):
        snap = K32.CreateToolhelp32Snapshot(0x4, 0)                          # TH32CS_SNAPTHREAD
        if not snap or snap == INVALID: return
        seen = set()
        try:
            te = TE32(); te.size = ctypes.sizeof(TE32); more = K32.Thread32First(snap, ctypes.byref(te))
            while more:
                if te.pid == self.pid:
                    seen.add(te.tid)
                    if te.tid not in self.th:
                        h = K32.OpenThread(0x0800, False, te.tid)                # THREAD_QUERY_LIMITED_INFORMATION
                        if h: self.th[te.tid] = h
                more = K32.Thread32Next(snap, ctypes.byref(te))
        finally: K32.CloseHandle(snap)
        for tid in [k for k in self.th if k not in seen]: K32.CloseHandle(self.th.pop(tid))

    def threads(self):
        if not self.pid: return {}
        if time.monotonic() - self.listed >= 5: self._list(); self.listed = time.monotonic()
        out = {}; t = (ctypes.c_ulonglong * 4)()
        for tid, h in self.th.items():
            if K32.GetThreadTimes(h, ctypes.byref(t, 0), ctypes.byref(t, 8), ctypes.byref(t, 16), ctypes.byref(t, 24)):
                out[tid] = (t[2] + t[3]) / 1e7
        return out

    def close(self):
        for h in self.th.values(): K32.CloseHandle(h)
        if self.ph: K32.CloseHandle(self.ph)
        self.th = {}; self.ph = None


class Tape:
    """What the TapeRecorder appended since the last call: (events, median delay s, max delay s). The first call only finds the end."""
    def __init__(self, path): self.path = path; self.pos = None

    def read(self):
        try:
            with open(self.path, 'rb') as f:
                f.seek(0, 2); end = f.tell()
                if self.pos is None or end < self.pos: self.pos = end; return 0, None, None
                start = max(self.pos, end - (16 << 20)); f.seek(start); buf = f.read(end - start)
        except (OSError, TypeError): return 0, None, None
        cut = buf.rfind(b'\n') + 1; self.pos = start + cut; lags = []
        for line in buf[:cut].split(b'\n'):
            if len(line) < 56 or line[:1] not in b'TAB' or line[1:2] != b',': continue
            try: x = secs(line[29:55]) - secs(line[2:28])
            except ValueError: continue
            lags.append(x + 86400 if x < -43200 else x)                       # across midnight UTC
        if not lags: return 0, None, None
        lags.sort(); return len(lags), lags[len(lags) // 2], lags[-1]


def wrap(a, b):
    return None if a is None or b is None else (b - a) % (1 << 32)


def rithmic_peers(pid):
    """IPv4 addresses NinjaTrader holds TCP connections to, other than port 443 (web services): Rithmic's servers."""
    if not pid: return []
    try:
        p = subprocess.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True, text=True, creationflags=NOWIN, timeout=20)
    except Exception: return []
    out = []
    for line in p.stdout.splitlines():
        f = line.split()
        if len(f) == 5 and f[3] == 'ESTABLISHED' and f[4] == str(pid) and ':' in f[2]:
            ip, port = f[2].rsplit(':', 1)
            if port != '443' and not ip.startswith(('127.', '[')) and ip not in out: out.append(ip)
    return out


MD_RX = re.compile(rb'^(\d{9,11}\.\d+) .*login_agent_tp\w* : Peer Address : ([^\s(]+)\(([\d.]+)\):(\d+)')


def md_servers(folder=EXPORTS, days=60):
    """Every market-data server Rithmic gave NinjaTrader at login in the last `days`, oldest first: (ET datetime, host, ip,
    connection name). Rithmic's API logs each login's 'login_agent_tp_... : Peer Address : host(ip):port' (tp = ticker plant, the
    price feed) in <NinjaTrader 8>\\trace\\Rithmic.<connection>.<date>.000; `folder` is agent_exports, beside trace."""
    trace = os.path.join(os.path.dirname(os.path.abspath(folder)), 'trace'); out = []; cut = time.time() - days * 86400
    try: names = [n for n in os.listdir(trace) if n.startswith('Rithmic.')]
    except OSError: return out
    for n in names:
        try:
            p = os.path.join(trace, n)
            if os.path.getmtime(p) < cut: continue
            with open(p, 'rb') as f: b = f.read()
        except OSError: continue
        for line in b.split(b'\n'):
            if b'Peer Address' not in line or b'login_agent_tp' not in line: continue
            m = MD_RX.match(line)
            if m: out.append((dt.datetime.fromtimestamp(float(m.group(1))), m.group(2).decode('ascii', 'replace'), m.group(3).decode(),
                              n.split('.')[1] if n.count('.') >= 3 else ''))
    out.sort(); return out


def md_at(servers, t):
    """The market-data server in use at ET datetime t (the last login before it); None when unknown."""
    i = bisect.bisect_right([s[0] for s in servers], t)
    return servers[i - 1] if i else None


def md_note(servers, t):
    """'host (ip) since <ET> - <how often Rithmic gave it before>' for the server in use at t; '' when unknown."""
    s = md_at(servers, t)
    if not s: return ''
    before = [x for x in servers if x[0] < s[0]]; seen = sum(x[1] == s[1] for x in before)
    days = (s[0] - before[0][0]).days if before else 0
    return (f"{s[1]} ({s[2]}) since {s[0]:%a %m-%d %H:%M} ET - "
            + ("the first login in the logs" if not before else
               f"first time in {len(before)} logins over {days} days" if not seen else f"given {seen} time(s) in {len(before)} logins over {days} days"))


class Pinger:
    """Pings each address every PING_S seconds on a background thread (IcmpSendEcho: no admin rights needed); take() returns
    'ip=ms;...' once per round (ms empty when there was no reply), else ''."""
    def __init__(self, ips):
        import threading
        self.ips = ips; self.res = ''; self.lock = threading.Lock(); self.stop = threading.Event()
        self.t = threading.Thread(target=self.loop, daemon=True)
        if ips: self.t.start()

    def ping(self, h, ip, buf):
        try:
            n = IPH.IcmpSendEcho(h, WT.DWORD(struct.unpack('<I', socket.inet_aton(ip))[0]), b'np', 2, None, buf, len(buf), 800)
            if n and struct.unpack_from('<I', buf, 4)[0] == 0: return struct.unpack_from('<I', buf, 8)[0]
        except Exception: pass
        return None

    def loop(self):
        IPH.IcmpCreateFile.restype = ctypes.c_void_p; IPH.IcmpCloseHandle.argtypes = [ctypes.c_void_p]
        IPH.IcmpSendEcho.argtypes = [ctypes.c_void_p, WT.DWORD, ctypes.c_char_p, WT.WORD, ctypes.c_void_p, ctypes.c_void_p, WT.DWORD, WT.DWORD]
        h = IPH.IcmpCreateFile(); buf = ctypes.create_string_buffer(256)
        if not h or h == INVALID: return
        try:
            while not self.stop.is_set():
                r = ';'.join(f"{ip}={'' if (ms := self.ping(h, ip, buf)) is None else ms}" for ip in self.ips)
                with self.lock: self.res = r
                self.stop.wait(PING_S)
        finally: IPH.IcmpCloseHandle(h)

    def take(self):
        with self.lock: r, self.res = self.res, ''
        return r

    def close(self): self.stop.set()


def sample(folder=EXPORTS, until=None, seconds=None, pid=None, tape=None, out=None, echo=False):
    """One row a second until time.monotonic() reaches `until` (or for `seconds`), appended to `out` (default
    records\\perf_probe_<today>.csv). Returns the number of rows written."""
    t0 = time.monotonic(); until = until if until is not None else t0 + (seconds or 60)
    if pid is None: pid = nt_pid()
    if tape is None: tape = tape_path(folder, dt.datetime.now())
    out = out or os.path.join(folder, 'records', f"perf_probe_{dt.date.today():%Y-%m-%d}.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    ifx = route_interface(); tp = Tape(tape); pr = Proc(pid); pg = Pinger(rithmic_peers(pid)); n = 0
    def read():
        return {'m': time.monotonic(), 'wall': dt.datetime.now(), 'net': net_in(ifx), 'tcp': tcp_counts(), 'cpu': pr.cpu(),
                'th': pr.threads(), 'sys': system_times(), 'tape': tp.read()}
    try:
        new = not os.path.exists(out)
        if not new:                                   # a file started by an older version: bring its header up to date
            with open(out, encoding='utf-8') as f: old = f.read()
            if old.split('\n', 1)[0].strip() != ','.join(COLS):
                with open(out + '.tmp', 'w', encoding='utf-8', newline='') as f: f.write(','.join(COLS) + '\n' + old.split('\n', 1)[-1])
                os.replace(out + '.tmp', out)
        with open(out, 'a', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            if new: w.writerow(COLS)
            prev = read()
            while prev['m'] + 1.0 < until:
                time.sleep(max(0.0, prev['m'] + 1.0 - time.monotonic()))
                cur = read(); d = cur['m'] - prev['m']
                ev, lag, lmax = cur['tape']; nb = wrap(prev['net'], cur['net'])
                seg = [wrap(a, b) for a, b in zip(prev['tcp'], cur['tcp'])]
                ins = None if seg[0] is None else seg[0] + (seg[2] or 0); rtx = None if seg[1] is None else seg[1] + (seg[3] or 0)
                top = max([(c - prev['th'][k]) for k, c in cur['th'].items() if k in prev['th']] or [None], key=lambda v: v or 0)
                busy = None
                if cur['sys'] and prev['sys']:
                    di, db = cur['sys'][0] - prev['sys'][0], cur['sys'][1] - prev['sys'][1]
                    busy = 100.0 * db / (db + di) if db + di > 0 else None
                f1 = lambda v, fmt: '' if v is None else format(v, fmt)
                row = [cur['wall'].strftime('%Y-%m-%d %H:%M:%S.%f')[:-3], f1(lag, '.2f'), f1(lmax, '.2f'), f1(ev / d, '.0f'),
                       f1(None if nb is None else nb / d / 1000, '.1f'), f1(None if ins is None else ins / d, '.0f'),
                       f1(None if rtx is None else rtx / d, '.1f'),
                       f1(None if cur['cpu'] is None or prev['cpu'] is None else (cur['cpu'] - prev['cpu']) / d, '.2f'),
                       f1(None if top is None else 100 * top / d, '.0f'), f1(busy, '.0f'), pg.take()]
                w.writerow(row); n += 1
                if echo: print(','.join(row))
                if n % 10 == 0: f.flush()
                prev = cur
    finally: pr.close(); pg.close()
    return n


# ---------------------------------------------------------------- report

def load(folder, day):
    """The day's probe rows: (ET datetime, {column: float or None})."""
    p = os.path.join(folder, 'records', f"perf_probe_{day}.csv"); rows = []
    try:
        for r in csv.DictReader(open(p, encoding='utf-8')):
            try:
                t = dt.datetime.strptime(r['time_et'], '%Y-%m-%d %H:%M:%S.%f')
                v = {k: (float(r[k]) if r.get(k) not in (None, '') else None) for k in COLS[1:-1]}
            except (ValueError, KeyError, TypeError): continue
            v['rtt'] = {ip: float(ms) for ip, _, ms in (x.partition('=') for x in (r.get('rtt_ms') or '').split(';')) if ms}
            rows.append((t, v))
    except OSError: pass
    rows.sort(key=lambda x: x[0]); out = []
    for t, v in rows:                                 # two probes at once (a manual run beside the watchdog's): keep one
        if not out or (t - out[-1][0]).total_seconds() >= 0.5: out.append((t, v))
    return out


def tape_times(path, t0, t1):
    """(sorted exchange times, sorted receive times) in seconds after midnight UTC of the events received between t0 and t1 (same
    units), read from the tape by binary search on the receive time; None when the tape is missing."""
    try: size = os.path.getsize(path)
    except (OSError, TypeError): return None
    hh = lambda s: '%02d:%02d:%09.6f' % (s // 3600, s % 3600 // 60, s % 60)
    feed, recv = [], []
    with open(path, 'rb') as f:
        lo, hi = 0, size
        while hi - lo > 1 << 16:                     # lines are appended in receive order
            mid = (lo + hi) // 2; f.seek(mid); f.readline(); line = f.readline()
            if len(line) < 56: hi = mid; continue
            if line[40:55].decode('ascii', 'replace') < hh(t0): lo = mid
            else: hi = mid
        f.seek(lo)
        if lo: f.readline()
        for line in f:
            if len(line) < 56 or line[:1] not in b'TAB' or line[1:2] != b',': continue
            try: r = secs(line[29:55])
            except ValueError: continue
            if r < t0: continue
            if r >= t1: break
            recv.append(r); feed.append(secs(line[2:28]))
    feed.sort(); return feed, recv


def ols(pts):
    n = len(pts)
    if n < 30: return None
    mx, my = sum(x for x, _ in pts) / n, sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x, _ in pts); syy = sum((y - my) ** 2 for _, y in pts)
    if sxx <= 0 or syy <= 0: return None
    k = sum((x - mx) * (y - my) for x, y in pts) / sxx; a = my - k * mx
    return a, k, k * k * sxx / syy


def fit(xs, ys):
    """Least squares y = a + k x after dropping outliers (a download burst: 10-27 10:37:09 had 7.5 MB in one second):
    (a, k, r2) or None."""
    pts = list(zip(xs, ys))
    for _ in range(3):
        r = ols(pts)
        if r is None: return None
        res = [y - r[0] - r[1] * x for x, y in pts]; mad = sorted(abs(e) for e in res)[len(res) // 2] or 1e-9
        keep = [p for p, e in zip(pts, res) if abs(e) <= 6 * mad]
        if len(keep) == len(pts): return r
        pts = keep
    return ols(pts)


def report(folder, day, tape=None):
    """Lines for the daily review; [] when the day has no probe file."""
    rows = load(folder, day)
    if not rows: return []
    lags = [v['lag_s'] for _, v in rows if v['lag_s'] is not None]
    head = (f"one-second probe: {len(rows):,} s sampled between {rows[0][0]:%H:%M} and {rows[-1][0]:%H:%M} ET; data delay max "
            f"{max(lags):.1f} s" if lags else f"one-second probe: {len(rows):,} s sampled, no tape data")
    eps, cur = [], []
    for t, v in rows:
        if v['lag_s'] is not None and v['lag_s'] >= LATE_S:
            if cur and (t - cur[-1][0]).total_seconds() > 20: eps.append(cur); cur = []
            cur.append((t, v))
    if cur: eps.append(cur)
    if not eps: return [head + f" (never {LATE_S:.0f} s late)"]
    out = [head + f"; {len(eps)} stretch(es) {LATE_S:.0f} s or more late" + (" (first 5 analysed)" if len(eps) > 5 else '')]
    u = lambda t: (t.astimezone(dt.timezone.utc) - t.astimezone(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds()
    if tape is None: tape = tape_path(folder, eps[0][0][0])
    base = sorted(v['lag_s'] for _, v in rows if v['lag_s'] is not None and v['lag_s'] < 1)
    base = base[len(base) // 2] if base else 0.0                              # normal delay incl. the PC clock's error
    servers = md_servers(folder)
    for ep in eps[:5]:
        t0, t1 = ep[0][0], ep[-1][0]; mx = max(v['lag_s'] for _, v in ep)
        # the tape around the stretch only (a whole session is ~3 GB): 4 min before, 5 min after
        win = [(t, v) for t, v in rows if t0 - dt.timedelta(seconds=240) <= t <= t1 + dt.timedelta(seconds=300)]
        tt = tape_times(tape, u(win[0][0]) - 2, u(win[-1][0]) + 1)
        rates = {}                                                            # row time -> (exchange events/s, intake events/s)
        if tt:
            feed, recv = tt
            for (tp, _), (t, v) in zip(win, win[1:]):
                d = (t - tp).total_seconds()
                if not 0.5 <= d <= 2: continue
                a_, b_ = u(tp), u(t)
                ex = bisect.bisect_left(feed, b_ - base) - bisect.bisect_left(feed, a_ - base)
                ri = bisect.bisect_left(recv, b_) - bisect.bisect_left(recv, a_)
                rates[t] = (ex / d, ri / d)
        norm = [(rates[t][1], v['net_in_kb_s']) for t, v in win if t in rates and v['net_in_kb_s'] is not None
                and v['lag_s'] is not None and v['lag_s'] < 1]
        line_fit = fit([x for x, _ in norm], [y for _, y in norm]) if norm else None
        span = [(t, v) for t, v in win if t0 - dt.timedelta(seconds=30) <= t <= t1 + dt.timedelta(seconds=30)]
        tops = [v['nt_top_thread_pct'] for _, v in ep if v['nt_top_thread_pct'] is not None]
        busy = [v['pc_busy_pct'] for _, v in ep if v['pc_busy_pct'] is not None]
        rtx = [v['tcp_retrans_s'] for _, v in ep if v['tcp_retrans_s'] is not None]
        ex_pk = max([rates[t][0] for t, _ in ep if t in rates] or [0]); in_pk = max([rates[t][1] for t, _ in ep if t in rates] or [0])
        def rtts(sel):                                 # median ping per Rithmic server over the selected rows
            g = {}
            for _, v in sel:
                for ip, ms in v['rtt'].items(): g.setdefault(ip, []).append(ms)
            return '/'.join(f"{sorted(x)[len(x) // 2]:.0f}" for _, x in sorted(g.items())) if g else ''
        r_ep = rtts(ep); r_ok = rtts([(t, v) for t, v in win if v['lag_s'] is not None and v['lag_s'] < 1])
        sv = md_at(servers, t0)
        s = (f"  data {LATE_S:.0f}+ s late {t0:%H:%M:%S}-{t1:%H:%M:%S} (max {mx:.1f} s"
             + (f"; exchange up to {ex_pk:,.0f} events/s, NinjaTrader took in up to {in_pk:,.0f}/s" if rates else '')
             + (f"; Rithmic data server {sv[1]}, since {sv[0]:%a %H:%M}" if sv else '') + "): "
             + (f"busiest NinjaTrader thread max {max(tops):.0f}% of a core, " if tops else '')
             + (f"PC {sum(busy) / len(busy):.0f}% busy, " if busy else '') + (f"TCP retransmits {max(rtx):.0f}/s max" if rtx else '')
             + (f", ping to Rithmic {r_ep} ms" + (f" (on time: {r_ok} ms)" if r_ok else '') if r_ep else '') + ". ")
        if tops and sum(x >= MAXED_PCT for x in tops) >= len(tops) / 2:
            s += "VERDICT: one NinjaTrader thread used a full core for most of it - NinjaTrader itself could not keep up."
        elif busy and sum(busy) / len(busy) >= 80:
            s += "VERDICT: the PC was busy - perf_log.csv names the busiest programs."
        elif not tt: s += "Where: the tape for this day is missing, so it cannot be told."
        elif not line_fit or line_fit[1] <= 0 or line_fit[2] < 0.3:
            s += "Where: network bytes do not follow the MNQ data here (other traffic), so it cannot be told."
        else:
            a, k, _ = line_fit; e_up = e_in = 0.0; m = 0
            for t, v in span:
                if t not in rates or v['net_in_kb_s'] is None: continue
                ex, ri = rates[t]
                if max(ex, ri) <= 0 or abs(ex - ri) < 0.15 * max(ex, ri): continue      # only seconds where the two differ
                p_up, p_nt = a + k * ri, a + k * ex
                if min(p_up, p_nt, v['net_in_kb_s']) <= 0: continue
                eu, en = abs(math.log(v['net_in_kb_s'] / p_up)), abs(math.log(v['net_in_kb_s'] / p_nt))
                if min(eu, en) > math.log(3): continue                                  # other traffic (a download)
                e_up += eu; e_in += en; m += 1
            if m < 15: s += f"Where: only {m} informative seconds, so it cannot be told."
            elif e_up < 0.75 * e_in:
                s += ("VERDICT: network bytes followed NinjaTrader's intake, not the exchange - the data reached this PC late "
                      f"(Rithmic's data server or the internet line); NinjaTrader kept up with what arrived ({m} s compared).")
            elif e_in < 0.75 * e_up:
                s += ("VERDICT: network bytes followed the exchange, not NinjaTrader's intake - the data was on this PC on time "
                      f"and waited inside NinjaTrader ({m} s compared).")
            else: s += f"Where: unclear - network bytes fit both ({m} s compared)."
        out.append(s)
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--folder', default=EXPORTS); ap.add_argument('--seconds', type=float)
    ap.add_argument('--report'); ap.add_argument('--servers', action='store_true'); a = ap.parse_args()
    if a.report: print('\n'.join(report(a.folder, a.report)) or f"no probe file for {a.report}"); return
    if a.servers:
        for t, host, ip, conn in md_servers(a.folder): print(f"{t:%Y-%m-%d %H:%M:%S} ET  {conn}: {host} ({ip})")
        print('now: ' + (md_note(md_servers(a.folder), dt.datetime.now()) or 'unknown')); return
    n = sample(a.folder, seconds=a.seconds or 20, echo=True); print(f"{n} rows")


if __name__ == '__main__':
    main()
