r"""Screens the fetched transcripts (regime_work\yt_tx) for mechanical, testable content before the rule extraction:
rule-language density, futures/index relevance, and size; writes regime_work\yt_screen.csv and shard lists for the extractors.
    python yt_screen.py [n_shards]
"""
import os, sys, json, re
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); WORK = os.path.join(HERE, 'regime_work'); TX = os.path.join(WORK, 'yt_tx')
import pandas as pd

RULE = re.compile(r'\b(stop loss|stop|take profit|target|entry|enter|exit|risk[- ]reward|\d+ ?r\b|candle close|closes? (?:above|below)|'
                  r'minute|ema|sma|moving average|vwap|rsi|macd|bollinger|atr|supertrend|breakout|break of|opening range|first candle|'
                  r'high of|low of|previous day|session|london|new york|asia|backtest|win ?rate|profit factor|trades)\b', re.I)
VAGUE = re.compile(r'\b(discretion|discretionary|feel|intuition|it depends|gut|read the tape|footprint|order ?flow|dom\b|delta)\b', re.I)
FUT = re.compile(r'\b(nq|nasdaq|es\b|s&p|spx|spy|qqq|futures|mnq|mes|us100|nas100|index|indices|us30|dow)\b', re.I)
ALREADY = {'iLmF1DSP2Ls', 'q1eFMTC3VL8', 'EL9R9DZ0YGs', 'IGxUa-4hw5c', 'j_t7aEciHfA', 'PgTjr4ocBZI', 'C4NIZvVCqc4', '155VZ1x0yKI', '3L8xdh3oPm4',
           'VOuddq0jM0w', 'h87RSv5xeOY', 'ab-nQBI2OcU', 'EUYZeR7Ag6E', 'KKNLrcpKz_M', 'v3CnTJuV8n4'}


def main():
    shards = int(sys.argv[1]) if len(sys.argv) > 1 else 6; rows = []
    for f in os.listdir(TX):
        d = json.load(open(os.path.join(TX, f), encoding='utf-8')); t = d.get('transcript') or ''; w = max(len(t.split()), 1)
        rows.append(dict(id=d['id'], title=d.get('title'), channel=d.get('channel'), views=d.get('views'), minutes=round(d.get('seconds', 0) / 60),
                         chars=len(t), rule_per_1k=len(RULE.findall(t)) / w * 1000, vague_per_1k=len(VAGUE.findall(t)) / w * 1000,
                         fut=len(FUT.findall(t + ' ' + (d.get('title') or '') + ' ' + (d.get('desc') or ''))), already=d['id'] in ALREADY,
                         path=os.path.join(TX, f)))
    df = pd.DataFrame(rows)
    df['keep'] = (df.chars > 1500) & (~df.already) & (df.rule_per_1k >= 12) & (df.vague_per_1k < 4) & (df.fut >= 1)
    df = df.sort_values(['keep', 'views'], ascending=False); df.to_csv(os.path.join(WORK, 'yt_screen.csv'), index=False)
    k = df[df.keep].copy(); k['shard'] = range(len(k)); k['shard'] %= shards
    for s in range(shards):
        open(os.path.join(WORK, f'yt_shard_{s}.txt'), 'w').write('\n'.join(k[k.shard == s].path))
    print(f"transcripts {len(df)}, with text {(df.chars > 1500).sum()}, kept {len(k)} -> {shards} shards "
          f"(chars per shard: {', '.join(str(int(k[k.shard == s].chars.sum() / 1000)) + 'k' for s in range(shards))})")


if __name__ == '__main__':
    main()
