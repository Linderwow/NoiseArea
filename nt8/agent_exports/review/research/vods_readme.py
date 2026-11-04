"""Write D:\\stream_capture\\vods\\README.txt: the download list for Da Captain Trader's recent streams."""
import json
from pathlib import Path

d = json.load(open(r"D:\stream_capture\DaCaptainTrader_\_dig\streams.json", encoding="utf-8"))
ents = [e for e in d["entries"] if e.get("live_status") == "was_live" and (e.get("duration") or 0) >= 3600][:40]
out_dir = r"D:\stream_capture\vods"
fmt = '"bv*[height=720][ext=mp4]"'
lines = [
    "Da Captain Trader - streams to download (newest first; ids are the YouTube video ids)",
    "",
    "Run in a normal PowerShell or cmd window. 720p video only, no audio. Measured: the 6h48 stream of 11/3 came to 0.7 GB,",
    "so a 12-hour stream is about 1.2 GB (static chart screens compress very well).",
    "EASIEST: python download_streams.py --max 60   (one by one, newest first, verifies each file, skips what is done)",
    "yt-dlp is installed for your Python (python -m yt_dlp). One command per stream:",
    "",
]
for e in ents:
    dur = e.get("duration") or 0
    lines.append(f'python -m yt_dlp -f {fmt} -o "{out_dir}\\%(id)s.mp4" https://www.youtube.com/watch?v={e["id"]}'
                 f'    # {e["title"][-18:]}  {dur // 3600}h{(dur % 3600) // 60:02d}')
lines += [
    "",
    "Or the newest 20 in one go:",
    "",
    f'python -m yt_dlp -f {fmt} -o "{out_dir}\\%(id)s.mp4" ' + " ".join(f'https://www.youtube.com/watch?v={e["id"]}' for e in ents[:20]),
    "",
    "When a file is complete I read it with:",
    '  stream_capture.py --file "D:\\stream_capture\\vods\\<id>.mp4"   (about 1 hour of PC time per 12-hour stream, a frame every 5 s)',
    "Results land in D:\\stream_capture\\DaCaptainTrader_\\vod\\<id>\\ (events, a frame of every trade, state log); the .mp4 can be deleted after.",
    "D: has 1.4 TB free.",
]
Path(out_dir).mkdir(parents=True, exist_ok=True)
Path(out_dir, "README.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines[:7]))
print(f"... {len(ents)} streams listed -> {out_dir}\\README.txt")
