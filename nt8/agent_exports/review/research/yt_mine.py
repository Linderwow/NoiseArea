r"""YouTube miner for SOURCES2: searches YouTube (innertube search API, as the website does), ranks the videos, and saves the
description + English captions of the selected ones (innertube player API, iOS client - nothing is played, no audio, no sign-in).
Transcripts are kept locally for rule extraction only (regime_work\yt_tx\, not redistributed).
    python yt_mine.py search          -> regime_work\yt_pool.csv  (every query in QUERIES, deduplicated)
    python yt_mine.py fetch N         -> transcripts of the N best-ranked videos not yet fetched -> regime_work\yt_tx\<id>.json
"""
import os, sys, json, re, time, urllib.request, urllib.parse
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); WORK = os.path.join(HERE, 'regime_work'); TX = os.path.join(WORK, 'yt_tx')
POOL = os.path.join(WORK, 'yt_pool.csv')
import pandas as pd

QUERIES = [
 'NQ futures strategy backtest', 'nasdaq futures strategy backtested 100 trades', 'I backtested NQ strategy', 'NQ opening range breakout backtest',
 'ES futures strategy backtest years', 'mechanical day trading strategy futures backtest', 'algorithmic trading strategy nasdaq futures results',
 'NinjaTrader strategy backtest NQ', 'TradingView strategy NQ1! backtest', 'tradestation ES strategy backtest', 'Kevin Davey futures strategy',
 'Quantified Strategies nasdaq', 'Concretum intraday momentum', 'larry williams volatility breakout', 'linda raschke strategy', 'Tomas Nesnidal breakout futures',
 'Unger Academy strategy futures', 'Jeff Swanson trading system ES', 'Data Trader backtest futures', 'backtested 1000 trades day trading strategy',
 'initial balance NQ backtest', 'NQ power hour strategy', 'CPI day NQ strategy', 'first 5 minute candle strategy NQ backtest', 'inside bar strategy futures backtest',
 'gap and go futures backtest', 'Bollinger squeeze futures backtest', 'supertrend strategy backtest futures', 'MACD strategy backtest 100 times',
 'RSI strategy tested 100 times', 'Heikin Ashi strategy tested 100 times', 'London open NQ strategy', 'overnight edge nasdaq futures', 'NY open NQ model backtest',
 'CRT model backtest NQ', 'turtle soup strategy backtest', 'holy grail strategy raschke', '3 bar play strategy futures', 'VWAP reclaim strategy NQ',
 'anchored VWAP strategy futures backtest', 'quant trading strategy that works nasdaq', 'statistically proven trading strategy futures',
 'prop firm strategy NQ backtested', 'opening drive strategy futures', 'moving average crossover strategy tested 100 times',
 # second wave: systematic / quant channels and index-specific edges
 'StatOasis strategy futures', 'ProRealAlgos nasdaq strategy', 'Build Alpha strategy futures', 'algo trading nasdaq 100 strategy backtest 10 years',
 'nasdaq 100 seasonality strategy', 'S&P 500 futures intraday seasonality', 'end of day momentum strategy SPY', 'overnight strategy SPY QQQ backtest',
 'QQQ day trading strategy backtest', 'SPY 0DTE gamma intraday strategy', 'market on close imbalance strategy', 'VIX spike buy strategy backtest',
 'mean reversion strategy nasdaq backtest', 'trend following futures strategy backtest', 'breakout strategy NQ automated', 'futures trading bot NQ results',
 'nasdaq futures scalping algorithm', 'pre market high low breakout NQ', 'previous day high low strategy NQ backtest', 'NQ 1 minute scalping strategy backtest',
 'ES scalping strategy backtest', 'MNQ strategy backtest', 'micro nasdaq futures strategy', 'futures prop firm algo strategy', 'NQ statistics opening hour',
 'NQ daily range statistics', 'futures quant edge statistics', 'Larry Connors strategy backtest', 'Rob Carver trading system', 'Andrea Unger strategy',
]


def http_json(url, body=None, ua='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36'):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json', 'User-Agent': ua, 'Accept-Language': 'en-US,en'})
    for k in range(4):
        try: return json.loads(urllib.request.urlopen(req, timeout=40).read())
        except Exception as e:
            if k == 3: raise
            time.sleep(3 + 4 * k)


def search(q, pages=2):
    out = []; body = {'context': {'client': {'clientName': 'WEB', 'clientVersion': '2.20261019.01.00', 'hl': 'en', 'gl': 'US'}}, 'query': q}
    for p in range(pages):
        j = http_json('https://www.youtube.com/youtubei/v1/search?prettyPrint=false', body); cont = None
        def walk(o):
            nonlocal cont
            if isinstance(o, dict):
                if 'videoRenderer' in o:
                    v = o['videoRenderer']; vc = v.get('viewCountText', {}); vc = vc.get('simpleText') or ''.join(r.get('text', '') for r in vc.get('runs', []))
                    out.append(dict(id=v['videoId'], title=''.join(r['text'] for r in v['title'].get('runs', [])),
                                    channel=(v.get('ownerText', {}).get('runs') or [{}])[0].get('text', ''),
                                    views=int(re.sub(r'[^0-9]', '', vc) or 0), age=v.get('publishedTimeText', {}).get('simpleText', ''),
                                    length=v.get('lengthText', {}).get('simpleText', ''), query=q)); return
                if 'continuationCommand' in o: cont = o['continuationCommand'].get('token')
                for x in o.values(): walk(x)
            elif isinstance(o, list):
                for x in o: walk(x)
        walk(j)
        if not cont: break
        body = {'context': body['context'], 'continuation': cont}
    return out


def player(vid):
    body = {'videoId': vid, 'context': {'client': {'clientName': 'IOS', 'clientVersion': '20.10.4', 'deviceModel': 'iPhone16,2', 'hl': 'en'}}}
    j = http_json('https://www.youtube.com/youtubei/v1/player?prettyPrint=false', body,
                  ua='com.google.ios.youtube/20.10.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)')
    vd = j.get('videoDetails', {}); tracks = j.get('captions', {}).get('playerCaptionsTracklistRenderer', {}).get('captionTracks', [])
    tr = next((t for t in tracks if t.get('languageCode', '').startswith('en') and t.get('kind') != 'asr'), None) or \
         next((t for t in tracks if t.get('languageCode', '').startswith('en')), None)
    txt = ''
    if tr:
        jj = http_json(tr['baseUrl'] + '&fmt=json3')
        txt = re.sub(r'\s+', ' ', ''.join(''.join(s.get('utf8', '') for s in e['segs']) for e in jj.get('events', []) if 'segs' in e)).strip()
    return dict(id=vid, status=j.get('playabilityStatus', {}).get('status'), title=vd.get('title'), channel=vd.get('author'),
                views=int(vd.get('viewCount', 0) or 0), seconds=int(vd.get('lengthSeconds', 0) or 0), desc=vd.get('shortDescription', ''),
                caption_kind=(tr or {}).get('kind', 'manual' if tr else 'none'), transcript=txt)


RELEVANT = re.compile(r'\b(nq|nasdaq|es\b|s&p|spx|spy|qqq|futures|index|us100|nas100|us30|mnq|mes|orb|opening range|vwap|initial balance|'
                      r'backtest|tested|strategy|system|algo|quant|edge|model|setup|scalp|day trad)', re.I)
NOISE = re.compile(r'(?:forex only|crypto|bitcoin|gold\b|xauusd|eurusd|options income|wheel|dividend|stock pick|penny|how to backtest|tutorial|course with python|live trading|live stream)', re.I)


def rank(df):
    """Score = log views + bonuses for futures/index relevance and for systematic evidence words; demote noise and live streams."""
    import numpy as np
    t = df.title.fillna('')
    s = np.log10(df.views.clip(lower=10))
    s += 0.8 * t.str.contains(r'\b(?:NQ|Nasdaq|ES|S&P|SPY|QQQ|futures|MNQ|US100|NAS100)\b', case=False, regex=True)
    s += 0.6 * t.str.contains(r'(?:backtest|tested|\d{3,} trades|years of data|proven|statistic|quant|algo|data)', case=False, regex=True)
    s -= 2.0 * t.str.contains(NOISE.pattern, case=False, regex=True)
    s -= 3.0 * (df.length.fillna('') == '')                     # live / upcoming
    return s


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'search':
        rows = []
        for q in QUERIES:
            try: rows += search(q)
            except Exception as e: print('search failed', q, e)
            time.sleep(0.5)
        df = pd.DataFrame(rows).drop_duplicates('id'); df['score'] = rank(df); df = df.sort_values('score', ascending=False)
        df.to_csv(POOL, index=False); print(f"{len(QUERIES)} queries, {len(df)} unique videos -> {POOL}")
    elif cmd == 'fetch':
        n = int(sys.argv[2]); os.makedirs(TX, exist_ok=True); df = pd.read_csv(POOL)
        if len(sys.argv) > 3: df = df[df.id.isin(sys.argv[3].split(','))]
        done = {f[:-5] for f in os.listdir(TX)}; todo = [v for v in df.id if v not in done][:n]; ok = 0
        for vid in todo:
            try:
                d = player(vid); json.dump(d, open(os.path.join(TX, vid + '.json'), 'w', encoding='utf-8')); ok += bool(d['transcript'])
            except Exception as e: print('fetch failed', vid, e)
            time.sleep(0.8)
        print(f"fetched {len(todo)}, with transcript {ok}")
    else: print(__doc__)


if __name__ == '__main__':
    main()
