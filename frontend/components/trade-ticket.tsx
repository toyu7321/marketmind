'use client';

import {useEffect, useState} from 'react';
import {Check, RefreshCw, ShieldAlert} from 'lucide-react';
import {api, money} from '@/lib/api';
import {Badge, ErrorState, Panel, Skeleton} from './ui';

type Data = Record<string, any>;

export function TradeTicket() {
  const [portfolio, setPortfolio] = useState<Data>();
  const [form, setForm] = useState({symbol: 'NVDA', quantity: 10, side: 'BUY', order_type: 'limit', time_in_force: 'day', limit_price: 181.5, estimated_price: 181.5, event_risk: false});
  const [preview, setPreview] = useState<Data>();
  const [message, setMessage] = useState('');
  const [working, setWorking] = useState(false);
  const load = () => api<Data>('/portfolio').then(setPortfolio).catch(() => setMessage('Could not synchronize the paper account.'));
  useEffect(() => { void load(); }, []);
  if (!portfolio) return message ? <ErrorState retry={load}/> : <Skeleton/>;

  const update = (key: string, value: string | boolean) => setForm(current => ({...current, [key]: ['quantity', 'limit_price', 'estimated_price'].includes(key) ? Number(value) : value}));
  const payload = () => ({
    ...form, current_exposure: portfolio.equity * portfolio.exposure / 100, sector_exposure: 0,
    daily_pnl: 0, liquidity: 10000000,
  });
  const checkRisk = async () => {
    setWorking(true); setMessage('');
    try {
      setPreview(await api<Data>('/trading/preview', {method: 'POST', body: JSON.stringify(payload())}));
    } catch {
      setMessage('Risk preview failed. Check the order fields and local API health.');
    } finally {
      setWorking(false);
    }
  };
  const submit = async () => {
    if (!preview || preview.risk.decision !== 'APPROVED') return;
    if (!window.confirm('Submit this order to the configured Alpaca PAPER account? This is not a live order.')) return;
    setWorking(true);
    try {
      const result = await api<Data>('/trading/orders', {method: 'POST', body: JSON.stringify({...payload(), confirmed: true})});
      setMessage('Paper order submitted: ' + (result.order?.status || result.status || 'accepted') + '.');
      setPreview(undefined);
      load();
    } catch {
      setMessage('Paper order was not submitted. Re-check the risk decision and broker connection.');
    } finally {
      setWorking(false);
    }
  };

  return <><div className="page-heading"><div><span className="eyebrow">EXECUTION DESK / PAPER ONLY</span><h1>Trading & Risk</h1><p>Signals propose. The Risk Engine decides. Submission requires a separate human confirmation.</p></div><div className="heading-actions"><Badge tone="good">PAPER MODE</Badge><button className="btn" onClick={load}><RefreshCw/> Sync</button></div></div>
    {message && <p className="save-message">{message}</p>}
    <div className="trading-grid"><Panel title="Order Ticket" eyebrow="PAPER BROKER"><div className="form-grid order"><label>Symbol<input value={form.symbol} maxLength={8} onChange={event => update('symbol', event.target.value.toUpperCase())}/></label><label>Side<select value={form.side} onChange={event => update('side', event.target.value)}><option>BUY</option><option>SELL</option></select></label><label>Quantity<input type="number" min="1" value={form.quantity} onChange={event => update('quantity', event.target.value)}/></label><label>Order type<select value={form.order_type} onChange={event => update('order_type', event.target.value)}><option value="limit">Limit</option><option value="market">Market</option></select></label><label>Limit price<input type="number" disabled={form.order_type === 'market'} value={form.limit_price} onChange={event => update('limit_price', event.target.value)}/></label><label>Estimated price<input type="number" value={form.estimated_price} onChange={event => update('estimated_price', event.target.value)}/></label><label className="event-check"><input type="checkbox" checked={form.event_risk} onChange={event => update('event_risk', event.target.checked)}/> Material event risk</label></div><button className="btn primary full" onClick={checkRisk} disabled={working}><ShieldAlert/> {working ? 'Checking risk…' : 'Preview through Risk Engine'}</button></Panel>
      <Panel title="Risk Gate" eyebrow="AUTHORITATIVE CONTROL"><div className="risk-gate">{preview ? <><ShieldAlert/><b className={preview.risk.decision === 'APPROVED' ? 'positive' : 'negative'}>{preview.risk.decision}</b><span>{preview.risk.reasons.join(' · ')}</span><div>Position size: {preview.risk.position_percent}%</div>{preview.risk.decision === 'APPROVED' && <button className="btn primary full" onClick={submit} disabled={working}>Confirm & submit PAPER order</button>}</> : <><ShieldAlert/><b>Awaiting order preview</b><span>No order is sent until the Risk Engine approves it and you explicitly confirm paper submission.</span><div><Check/> Live trading remains disabled</div></>}</div></Panel>
      <Panel title="Recent Orders" eyebrow={portfolio.source + ' FILLS'} className="wide"><div className="rank-list">{portfolio.orders.map((item: Data, index: number) => <div key={index}><i>{item.side}</i><div><b>{item.symbol}</b><span>{item.quantity} shares · {item.status}</span></div><strong>{money(item.price)}</strong><Badge tone="good">PAPER</Badge></div>)}</div></Panel></div>
  </>;
}
