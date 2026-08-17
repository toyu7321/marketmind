'use client';

import {useEffect, useState} from 'react';
import {RefreshCw, Search, ShieldAlert} from 'lucide-react';
import {api, money} from '@/lib/api';
import {Badge, ErrorState, Panel, Skeleton} from './ui';

type Data = Record<string, any>;
const display = (value: number | null | undefined, suffix = '') => value === null || value === undefined ? '—' : String(value) + suffix;

export function OptionsTerminal() {
  const [symbol, setSymbol] = useState('NVDA');
  const [data, setData] = useState<Data>();
  const [failure, setFailure] = useState(false);
  const [type, setType] = useState('ALL');
  const [expiry, setExpiry] = useState('ALL');
  const load = () => {
    setFailure(false);
    api<Data>('/options/' + (symbol || 'NVDA')).then(setData).catch(() => setFailure(true));
  };
  useEffect(load, [symbol]);
  if (failure) return <ErrorState retry={load}/>;
  if (!data) return <Skeleton/>;
  const chain=Array.isArray(data.chain)?data.chain:[];
  const expirations = [...new Set(chain.map((item: Data) => item.expiration))] as string[];
  const rows = chain.filter((item: Data) => (type === 'ALL' || item.type === type) && (expiry === 'ALL' || item.expiration === expiry));

  return <><div className="page-heading"><div><span className="eyebrow">DERIVATIVES / RISK-DEFINED ANALYSIS</span><h1>Options Terminal</h1><p>Chain data, Greeks when supplied, and deterministic payoff summaries.</p></div><div className="heading-actions"><label className="symbol-input"><Search/><input value={symbol} maxLength={8} onChange={event => setSymbol(event.target.value.toUpperCase())}/></label></div></div>
    <div className="data-status"><b>OPTIONS DATA</b><span>{data.provider_status?.options_provider||'Options provider'} · {data.provider_status?.options_feed||'—'} · {data.provider_status?.options_status||data.mode}</span><small>{data.provider_status?.last_successful_request?`Last update ${new Date(data.provider_status.last_successful_request).toLocaleString()}`:data.message||'No successful options request yet'}</small></div><div className="metric-strip"><div><span>UNDERLYING</span><b>{money(data.spot)}</b><small>{data.quote_quality||'UNAVAILABLE'}</small></div><div><span>IV SUMMARY</span><b>{display(data.iv_rank, '%')}</b></div><div><span>PUT / CALL</span><b>{display(data.put_call_ratio)}</b></div><div><span>DATA</span><Badge tone={data.mode === 'LIVE' ? 'good' : data.mode === 'DEMO' ? 'warn' : 'bad'}>{data.mode}</Badge></div></div>
    <div className="toolbar options-toolbar"><div className="filter-pills"><button className={type === 'ALL' ? 'active' : ''} onClick={() => setType('ALL')}>All</button><button className={type === 'CALL' ? 'active' : ''} onClick={() => setType('CALL')}>Calls</button><button className={type === 'PUT' ? 'active' : ''} onClick={() => setType('PUT')}>Puts</button></div><select className="table-select" value={expiry} onChange={event => setExpiry(event.target.value)}><option value="ALL">All expirations</option>{expirations.map(item => <option value={item} key={item}>{item}</option>)}</select><button className="btn" onClick={load}><RefreshCw/> Refresh</button></div>
    <div className="options-grid"><Panel title={data.symbol + ' Option Chain'} eyebrow={rows.length + ' CONTRACTS · ' + data.mode + ' SOURCE'} className="table-panel">{rows.length?<div className="table-scroll"><table><thead><tr>{['Expiry', 'Strike', 'Type', 'Bid', 'Ask', 'Last', 'Volume', 'Open Int.', 'IV', 'Delta', 'Gamma', 'Theta', 'Vega'].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{rows.map((item: Data, index: number) => <tr key={(item.contract || item.expiration) + '-' + item.strike + '-' + item.type + '-' + index}><td>{item.expiration}</td><td><b>{item.strike}</b></td><td><Badge tone={item.type === 'CALL' ? 'good' : 'bad'}>{item.type}</Badge></td><td>{display(item.bid)}</td><td>{display(item.ask)}</td><td>{display(item.last)}</td><td>{display(item.volume)}</td><td>{display(item.open_interest)}</td><td>{display(item.iv, '%')}</td><td>{display(item.delta)}</td><td>{display(item.gamma)}</td><td className="negative">{display(item.theta)}</td><td>{display(item.vega)}</td></tr>)}</tbody></table></div>:<div className="source-empty"><b>Live options unavailable</b><span>{data.message||'The configured account/feed did not provide an option chain. No synthetic contracts are shown.'}</span></div>}</Panel>
      <Panel title="Strategy Lab" eyebrow="DETERMINISTIC PAYOFFS · NOT RECOMMENDATIONS"><div className="strategy-list">{data.strategies.length ? data.strategies.map((item: Data) => <article key={item.name}><div><b>{item.name}</b><Badge tone="warn">RISK DISCLOSED</Badge></div><div><span>Debit / credit <b>{money(item.debit ?? item.credit)}</b></span><span>Max profit <b className="positive">{item.max_profit === null ? 'Uncapped' : money(item.max_profit)}</b></span><span>Max loss <b className="negative">{money(item.max_loss)}</b></span><span>Breakeven <b>{money(item.breakeven)}</b></span></div><p><ShieldAlert/> {item.risk} Outcomes are not guaranteed.</p></article>) : <p className="panel-note">The connected source did not provide enough quoted contracts for payoff summaries.</p>}</div></Panel></div>
  </>;
}
