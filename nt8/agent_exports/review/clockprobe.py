r"""
clockprobe.py - measure this PC's clock against internet time servers and append it to agent_exports\records\clock_offset.csv
(2026-10-22, logging audit: the PC clock drifts ~72 ms/hour with Windows Time on Manual, every PC-stamped time inherits that, and
without an offset history past latencies cannot be corrected).

    python clockprobe.py        one measurement (run hourly by tools\autopush.py and nightly by archive_nightly.py)

Plain SNTP (RFC 4330) to three public servers; the median offset is recorded. offset_ms = server time - PC time (positive = the PC
clock is behind). Nothing but the standard 48-byte time request is sent.
"""
import os, sys, time, socket, struct, statistics, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, '..', 'records', 'clock_offset.csv'))
SERVERS = ('time.windows.com', 'time.nist.gov', 'pool.ntp.org')


def sntp(server, timeout=4):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(timeout)
    try:
        t0 = time.time(); s.sendto(b'\x1b' + 47 * b'\0', (server, 123)); data, _ = s.recvfrom(512); t3 = time.time()
    finally:
        s.close()
    ts = lambda b: struct.unpack('!II', b)[0] - 2208988800 + struct.unpack('!II', b)[1] / 2 ** 32
    t1, t2 = ts(data[32:40]), ts(data[40:48])
    return ((t1 - t0) + (t2 - t3)) / 2 * 1000, ((t3 - t0) - (t2 - t1)) * 1000


def main():
    got = []
    for sv in SERVERS:
        try: got.append((sv,) + sntp(sv))
        except Exception: pass
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    new = not os.path.exists(OUT)
    with open(OUT, 'a', encoding='utf-8') as f:
        if new: f.write('time_utc,offset_ms,delay_ms,servers_answered,detail\n')
        now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        if got:
            off = statistics.median(g[1] for g in got); dl = statistics.median(g[2] for g in got)
            f.write(f"{now},{off:.1f},{dl:.1f},{len(got)},{' '.join(f'{g[0]}={g[1]:.1f}' for g in got)}\n")
            print(f"PC clock offset {off:+.1f} ms (server - PC), round trip {dl:.1f} ms, {len(got)} servers")
        else:
            f.write(f"{now},,,0,no server answered\n"); print("no time server answered")


if __name__ == '__main__':
    main()
