import pandas as pd, numpy as np
from published import load_rth
def S1_trades(days, look=14, vm=1.0):
    dates=sorted(days); out=[]; hist=[]; prev_close=None
    for d in dates:
        g=days[d]; O=g.open.values; H=g.high.values; L=g.low.values; C=g.close.values; M=g.m.values; V=g.volume.values.astype(float); T=g.time.values
        o930=O[0]; mv={int(mm):abs(c/o930-1) for mm,c in zip(M,C)}
        if len(hist)>=look and prev_close is not None:
            tp=(H+L+C)/3; vw=np.cumsum(tp*V)/np.maximum(np.cumsum(V),1)
            pos=0; entry=None; et=None; pending=None; checks=set(range(600,931,30))
            end=int(M[-1]); flat_i=len(g)-1 if end>=960 else len(g)-2   # partial session: flat on the bar before the last
            for i in range(len(g)):
                if pending is not None:
                    newpos=pending
                    if pos!=0: out.append(dict(date=d,entry_time=et,exit_time=T[i],dir=pos,entry=entry,exit=O[i])); pos=0
                    if newpos!=0: entry=O[i]; et=T[i]; pos=newpos
                    pending=None
                if i>=flat_i:
                    if pos!=0: out.append(dict(date=d,entry_time=et,exit_time=T[i],dir=pos,entry=entry,exit=C[i])); pos=0
                    break
                mm=int(M[i])
                if mm not in checks: continue
                vals=[h[mm] for h in hist[-look:] if mm in h]
                if len(vals)<max(1,(look+1)//2): continue
                sig=float(np.mean(vals))
                ub=max(o930,prev_close)*(1+vm*sig); lb=min(o930,prev_close)*(1-vm*sig); p=C[i]
                if pos==0:
                    if p>ub: pending=1
                    elif p<lb: pending=-1
                elif pos==1:
                    if p<lb: pending=-1
                    elif p<max(ub,vw[i]): pending=0
                else:
                    if p>ub: pending=1
                    elif p>min(lb,vw[i]): pending=0
        hist.append(mv); prev_close=C[-1]
    t=pd.DataFrame(out); t['points']=(t.exit-t.entry)*t.dir
    return t
if __name__=='__main__':
    t=S1_trades(load_rth('bars_new.csv')); t.to_csv('s1_reference_trades_ntbars.csv',index=False)
    print("reference trades on NinjaTrader bars:", len(t), t.date.min(), "->", t.date.max(), "| gross points %.1f" % t.points.sum())
    t2=S1_trades(load_rth('mnq_db_5m.csv')); t2.to_csv('s1_reference_trades_databento.csv',index=False)
    print("reference trades on Databento bars:", len(t2), t2.date.min(), "->", t2.date.max(), "| gross points %.1f" % t2.points.sum())
