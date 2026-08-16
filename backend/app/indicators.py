"""Deterministic, dependency-light technical indicator engine."""
import numpy as np


def _a(values): return np.asarray(values, dtype=float)
def sma(values, period):
    a = _a(values)
    # Stable rounding avoids platform-specific binary tails in deterministic output.
    return np.convolve(a, np.ones(period) / period, mode="valid").round(12).tolist() if len(a) >= period else []
def ema(values, period):
    a = _a(values)
    if not len(a): return []
    alpha = 2 / (period + 1); out = [float(a[0])]
    for value in a[1:]: out.append(float(alpha * value + (1-alpha) * out[-1]))
    return out
def rsi(values, period=14):
    a = _a(values)
    if len(a) <= period: return []
    delta = np.diff(a); gains = np.maximum(delta, 0); losses = np.maximum(-delta, 0); out=[]
    for i in range(period, len(delta)+1):
        gain=gains[i-period:i].mean(); loss=losses[i-period:i].mean()
        out.append(float(100 if loss == 0 else 100-(100/(1+gain/loss))))
    return out
def macd(values, fast=12, slow=26, signal=9):
    f=np.array(ema(values,fast)); s=np.array(ema(values,slow)); line=f-s; sig=np.array(ema(line,signal))
    return {"macd":line.tolist(), "signal":sig.tolist(), "histogram":(line-sig).tolist()}
def atr(high, low, close, period=14):
    h,l,c=_a(high),_a(low),_a(close)
    tr=np.maximum(h[1:]-l[1:], np.maximum(abs(h[1:]-c[:-1]),abs(l[1:]-c[:-1])))
    return sma(tr,period)
def bollinger(values, period=20, deviations=2):
    a=_a(values); mid=sma(a,period); upper=[]; lower=[]
    for i,m in enumerate(mid):
        std=a[i:i+period].std(); upper.append(float(m+deviations*std)); lower.append(float(m-deviations*std))
    return {"middle":mid,"upper":upper,"lower":lower}
def rolling_volatility(values, period=20):
    returns=np.diff(np.log(_a(values))); return [float(np.std(returns[i:i+period])*np.sqrt(252)*100) for i in range(len(returns)-period+1)]
def rate_of_change(values, period=10):
    a=_a(values); return (((a[period:]/a[:-period])-1)*100).tolist() if len(a)>period else []
def support_resistance(values, window=20):
    a=_a(values)[-window:]; return {"support":float(a.min()),"resistance":float(a.max())}
def technical_snapshot(close, high=None, low=None, volume=None):
    a=_a(close); e20=ema(a,20)[-1]; e50=ema(a,50)[-1]; rs=rsi(a)[-1]; m=macd(a)
    sr=support_resistance(a); vol=rolling_volatility(a)[-1]
    alignment="bullish" if a[-1]>e20>e50 else "bearish" if a[-1]<e20<e50 else "mixed"
    return {"rsi":round(rs,2),"macd":round(m["histogram"][-1],3),"ema20":round(e20,2),"ema50":round(e50,2),
            "volatility":round(vol,2),"trend":alignment,**{k:round(v,2) for k,v in sr.items()},
            "volume_spike": bool(volume and volume[-1] > np.mean(volume[-20:])*1.5)}
