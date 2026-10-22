import sys, databento as db, pandas as pd, numpy as np
def build(files, out):
    parts=[db.DBNStore.from_file(f).to_df()[['instrument_id','open','high','low','close','volume']] for f in files]
    df=pd.concat(parts); df=df[~df.index.duplicated(keep='last')].sort_index()
    # difference back-adjustment at each contract switch (offset = first open of new - last close of old)
    sw=np.flatnonzero(df.instrument_id.values[1:]!=df.instrument_id.values[:-1])+1
    adj=np.zeros(len(df))
    for k in sw:
        off=df.open.iat[k]-df.close.iat[k-1]
        adj[:k]+=off
    rolls=[(df.index[k].strftime('%Y-%m-%d %H:%M'), round(df.open.iat[k]-df.close.iat[k-1],2)) for k in sw]
    for c in ('open','high','low','close'): df[c]=df[c].values+adj
    # UTC bar-open -> ET bar-close, 1m -> 5m stamped at close
    close_et=(df.index+pd.Timedelta(minutes=1)).tz_convert('America/New_York').tz_localize(None)
    df=df.assign(bucket=close_et.ceil('5min'))
    g=df.groupby('bucket')
    b=pd.DataFrame({'open':g.open.first(),'high':g.high.max(),'low':g.low.min(),'close':g.close.last(),'volume':g.volume.sum()})
    b.index.name='time'; b=b.reset_index()
    b.to_csv(out,index=False)
    return b, rolls
if __name__=='__main__':
    base='/mnt/user-data/uploads/databento_data/'
    b,r=build([base+'mnq_ohlcv1m_2018-01-01_2022-12-31.dbn.zst', base+'mnq_ohlcv1m_2023-01-01_2025-12-31.dbn.zst'],'mnq_db_5m.csv')
    print("MNQ 5m bars:", len(b), b.time.min(), "->", b.time.max(), "| rolls:", len(r)); print("  sample rolls:", r[:3], r[-3:])
    b,r=build([base+'MES_ohlcv-1m_2019-2026.dbn.zst'],'mes_db_5m.csv')
    print("MES 5m bars:", len(b), b.time.min(), "->", b.time.max(), "| rolls:", len(r)); print("  sample rolls:", r[:3], r[-3:])
