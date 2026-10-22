import pandas as pd, numpy as np
TICK=0.25
def load_rth(path):
    df=pd.read_csv(path); df['time']=pd.to_datetime(df.time)
    df['date']=df.time.dt.date; m=df.time.dt.hour*60+df.time.dt.minute; df['m']=m
    r=df[(m>9*60+30)&(m<=16*60)].copy()
    days={}
    for d,g in r.groupby('date'):
        if len(g)<30 or g.m.iloc[0]!=9*60+35: continue
        days[d]=g.reset_index(drop=True)
    return days
def S1(days, pv, slip=TICK, comm=0.5, vm=1.0, look=14):
    dates=sorted(days); out=[]; hist=[]; prev_close=None
    for d in dates:
        g=days[d]; O=g.open.values; H=g.high.values; L=g.low.values; C=g.close.values; M=g.m.values; V=g.volume.values.astype(float)
        o930=O[0]; mv={int(mm):abs(c/o930-1) for mm,c in zip(M,C)}
        if len(hist)>=look and prev_close is not None:
            tp=(H+L+C)/3; vw=np.cumsum(tp*V)/np.maximum(np.cumsum(V),1)
            pos=0; entry=None; pnl=0.0; ntr=0
            checks=set(range(10*60,15*60+31,30))
            pending=None
            end=int(M[-1]); flat_i=len(g)-1 if end>=960 else len(g)-2   # partial session: flat on the bar before the last
            for i in range(len(g)):
                if pending is not None:              # fill at this bar's open
                    act,newpos=pending; px=O[i]
                    if pos!=0:  pnl+=(px-slip*pos-entry)*pos*pv-comm; pos=0
                    if newpos!=0: entry=px+slip*newpos; pos=newpos; pnl-=comm; ntr+=1
                    pending=None
                if i>=flat_i:
                    if pos!=0: pnl+=(C[i]-slip*pos-entry)*pos*pv-comm; pos=0
                    break
                mm=int(M[i])
                if mm not in checks: continue
                vals=[h[mm] for h in hist[-look:] if mm in h]
                if len(vals)<max(1,(look+1)//2): continue
                sig=float(np.mean(vals))
                ub=max(o930,prev_close)*(1+vm*sig); lb=min(o930,prev_close)*(1-vm*sig); p=C[i]
                if pos==0:
                    if p>ub: pending=('enter',1)
                    elif p<lb: pending=('enter',-1)
                elif pos==1:
                    if p<lb: pending=('rev',-1)
                    elif p<max(ub,vw[i]): pending=('exit',0)
                elif pos==-1:
                    if p>ub: pending=('rev',1)
                    elif p>min(lb,vw[i]): pending=('exit',0)
            out.append(dict(date=d,pnl=pnl,trades=ntr))
        hist.append(mv); prev_close=C[-1]
    return pd.DataFrame(out)
def S2(days, pv, slip=TICK, comm=0.5):
    dates=sorted(days); out=[]; prev_close=None
    for d in dates:
        g=days[d]; M=list(g.m.values)
        if prev_close is not None and 10*60 in M and 15*60+30 in M and M[-1]==16*60:
            r=g.close.values[M.index(10*60)]/prev_close-1; k=M.index(15*60+30)
            sgn=int(np.sign(r))
            if sgn!=0:
                entry=g.open.values[k+1]+slip*sgn; ex=g.close.values[-1]-slip*sgn
                out.append(dict(date=d,pnl=(ex-entry)*sgn*pv-2*comm,trades=1))
            else: out.append(dict(date=d,pnl=0.0,trades=0))
        prev_close=g.close.values[-1]
    return pd.DataFrame(out)
def S3(days, pv, slip=TICK, comm=0.5, rr=10):
    out=[]
    for d in sorted(days):
        g=days[d]; O=g.open.values; H=g.high.values; L=g.low.values; C=g.close.values
        o,h,l,c=O[0],H[0],L[0],C[0]
        if c==o: out.append(dict(date=d,pnl=0.0,trades=0)); continue
        dr=1 if c>o else -1; stop=l if dr>0 else h
        entry=O[1]+slip*dr; risk=(entry-stop)*dr
        if risk<=0: out.append(dict(date=d,pnl=0.0,trades=0)); continue
        tgt=entry+dr*rr*risk; ex=None
        for i in range(1,len(g)):
            if (dr>0 and (L[i]<=stop or (i==1 and O[i]<=stop))) or (dr<0 and (H[i]>=stop or (i==1 and O[i]>=stop))): ex=stop-slip*dr; break
            if (dr>0 and H[i]>=tgt) or (dr<0 and L[i]<=tgt): ex=tgt; break
        if ex is None: ex=C[-1]-slip*dr
        out.append(dict(date=d,pnl=(ex-entry)*dr*pv-2*comm,trades=1))
    return pd.DataFrame(out)
def summary(res,label):
    res=res.copy(); res['y']=pd.to_datetime(res.date).dt.year
    p=res.pnl.values; tr=res[res.trades>0]
    t=p.mean()/(p.std(ddof=1)/np.sqrt(len(p))) if len(p)>2 else np.nan
    eq=np.cumsum(p); mdd=(np.maximum.accumulate(eq)-eq).max()
    a=res[res.y<=2022].pnl.sum(); b=res[(res.y>=2023)&(res.y<=2025)].pnl.sum()
    print(f"{label:34s} days {len(res):5d} trades {int(res.trades.sum()):5d} net ${p.sum():9,.0f} | 2019-22 ${a:8,.0f} | 2023-25 ${b:8,.0f} | t {t:+.2f} | maxDD ${mdd:7,.0f}")
    print("      by year $:", res.groupby('y').pnl.sum().round(0).astype(int).to_dict())
    return dict(net=p.sum(),a=a,b=b,t=t)
