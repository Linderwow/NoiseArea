import csv, math, sys
def load(paths):
    bars=[]
    for p in paths:
        for r in csv.DictReader(open(p)):
            bars.append((r['time_et'], float(r['open']), float(r['high']), float(r['low']), float(r['close'])))
    seen=set(); out=[]
    for b in bars:
        if b[0] in seen: continue
        seen.add(b[0]); out.append(b)
    return out
def mama(bars, fast=0.5, slow=0.05):
    n=len(bars); med=[(b[2]+b[3])/2 for b in bars]; inp=[b[4] for b in bars]
    z=lambda: [0.0]*n
    smooth,det,q1,i1,i2,q2,re,im,per,ph,M,F=[z() for _ in range(12)]
    for t in range(n):
        if t<6: continue
        if t==6: M[t]=med[t]; F[t]=inp[t]; continue
        p1=per[t-1]
        smooth[t]=(4*med[t]+3*med[t-1]+2*med[t-2]+med[t-3])/10
        g=lambda s,k: s[k] if k>=0 else 0.0
        det[t]=(0.0962*smooth[t]+0.5769*g(smooth,t-2)-0.5769*g(smooth,t-4)-0.0962*g(smooth,t-6))*(0.075*p1+0.54)
        q1[t]=(0.0962*det[t]+0.5769*g(det,t-2)-0.5769*g(det,t-4)-0.0962*g(det,t-6))*(0.075*p1+0.54)
        i1[t]=g(det,t-3)
        jI=(0.0962*i1[t]+0.5769*g(i1,t-2)-0.5769*g(i1,t-4)-0.0962*g(i1,t-6))*(0.075*p1+0.54)
        jQ=(0.0962*q1[t]+0.5769*g(q1,t-2)-0.5769*g(q1,t-4)-0.0962*g(q1,t-6))*(0.075*p1+0.54)
        i2[t]=i1[t]-jQ; q2[t]=q1[t]+jI
        i2[t]=0.2*i2[t]+0.8*i2[t-1]; q2[t]=0.2*q2[t]+0.8*q2[t-1]
        re[t]=i2[t]*i2[t-1]+q2[t]*q2[t-1]; im[t]=i2[t]*q2[t-1]-q2[t]*i2[t-1]
        re[t]=0.2*re[t]+0.8*re[t-1]; im[t]=0.2*im[t]+0.8*im[t-1]
        p0=per[t]
        if im[t]!=0 and re[t]!=0: p0=360/(180/math.pi*math.atan(im[t]/re[t]))
        if p0>1.5*p1: p0=1.5*p1
        if p0<0.67*p1: p0=0.67*p1
        if p0<6: p0=6
        if p0>50: p0=50
        per[t]=0.2*p0+0.8*p1
        if i1[t]!=0: ph[t]=180/math.pi*math.atan(q1[t]/i1[t])
        else: ph[t]=0.0  # NT Series default when unset is the previous? keep 0
        dp=ph[t-1]-ph[t]
        if dp<1: dp=1
        a=fast/dp
        if a<slow: a=slow
        M[t]=a*med[t]+(1-a)*M[t-1]; F[t]=0.5*a*M[t]+(1-0.5*a)*F[t-1]
    return M,F
if __name__=="__main__":
    base="D:/nt_recorder/MNQ_12-26/"
    bars=load([base+"2026-11-03_bars1minute.csv", base+"2026-11-04_bars1minute.csv"])
    M,F=mama(bars)
    lo,hi=sys.argv[1],sys.argv[2]
    for t in range(1,len(bars)):
        ts=bars[t][0]
        if not (lo<=ts[11:]<=hi and ts.startswith("2026-11-04")): continue
        c=bars[t][4]; flags=[]
        if (M[t]-F[t])*(M[t-1]-F[t-1])<0: flags.append("MAMAxFAMA "+("UP" if M[t]>F[t] else "DN"))
        if (c-M[t])*(bars[t-1][4]-M[t-1])<0: flags.append("C x MAMA "+("UP" if c>M[t] else "DN"))
        if (c-F[t])*(bars[t-1][4]-F[t-1])<0: flags.append("C x FAMA "+("UP" if c>F[t] else "DN"))
        if flags or ts[14:16] in ("00",):
            print(ts[11:16], "O %.2f H %.2f L %.2f C %.2f  MAMA %.2f FAMA %.2f  " % (bars[t][1],bars[t][2],bars[t][3],c,M[t],F[t]), " | ".join(flags))
