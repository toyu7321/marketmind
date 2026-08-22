'use client';

import {useEffect, useMemo, useState} from 'react';
import {RefreshCw, Search, ShieldAlert} from 'lucide-react';

import {money} from '@/lib/api';
import {useApiData} from '@/lib/data';
import {Badge, ErrorState, Panel, Skeleton} from './ui';

type Data = Record<string, any>;
const PAGE_SIZE = 30;
const display = (value: number | null | undefined, suffix = '') => value === null || value === undefined ? '—' : String(value) + suffix;

export function OptionsTerminal() {
  const [inputSymbol, setInputSymbol] = useState('NVDA');
  const [symbol, setSymbol] = useState('NVDA');
  const [type, setType] = useState('ALL');
  const [expiry, setExpiry] = useState('ALL');
  const [minimumStrike, setMinimumStrike] = useState('');
  const [maximumStrike, setMaximumStrike] = useState('');
  const [page, setPage] = useState(0);

  useEffect(() => {
    const pendingSymbol = inputSymbol.trim().toUpperCase();
    if (!pendingSymbol || pendingSymbol === symbol) return;
    const timer = window.setTimeout(() => setSymbol(pendingSymbol), 350);
    return () => window.clearTimeout(timer);
  }, [inputSymbol, symbol]);

  const {data, error, retry, isValidating} = useApiData<Data>('/options/' + symbol);
  const chain = useMemo(() => Array.isArray(data?.chain) ? data.chain : [], [data?.chain]);
  const expirations = useMemo(() => [...new Set(chain.map((item: Data) => item.expiration).filter(Boolean))] as string[], [chain]);
  const rows = useMemo(() => {
    const minimum = Number(minimumStrike);
    const maximum = Number(maximumStrike);
    return chain.filter((item: Data) => {
      const strike = Number(item.strike);
      return (type === 'ALL' || item.type === type)
        && (expiry === 'ALL' || item.expiration === expiry)
        && (!minimumStrike || (Number.isFinite(strike) && strike >= minimum))
        && (!maximumStrike || (Number.isFinite(strike) && strike <= maximum));
    });
  }, [chain, expiry, maximumStrike, minimumStrike, type]);
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const visibleRows = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  useEffect(() => { setPage(0); }, [expiry, maximumStrike, minimumStrike, symbol, type]);
  useEffect(() => { if (page >= pageCount) setPage(pageCount - 1); }, [page, pageCount]);

  if (error) return <ErrorState retry={retry}/>;
  if (!data) return <Skeleton/>;

  const pageStart = rows.length ? page * PAGE_SIZE + 1 : 0;
  const pageEnd = Math.min((page + 1) * PAGE_SIZE, rows.length);
  const dataTone = data.mode === 'LIVE' ? 'good' : data.mode === 'DEMO' ? 'warn' : 'bad';

  return <><div className="page-heading"><div><span className="eyebrow">DERIVATIVES / RISK-DEFINED ANALYSIS</span><h1>Options Terminal</h1><p>Chain data, Greeks when supplied, and deterministic payoff summaries.</p></div><div className="heading-actions"><label className="symbol-input"><Search/><input value={inputSymbol} maxLength={8} onChange={event => setInputSymbol(event.target.value.toUpperCase())} onKeyDown={event => { if (event.key === 'Enter') setSymbol(inputSymbol.trim().toUpperCase() || 'NVDA'); }}/></label></div></div>
    <div className="data-status"><b>OPTIONS DATA</b><span>{data.provider_status?.options_provider || 'Options provider'} · {data.provider_status?.options_feed || '—'} · {data.provider_status?.options_status || data.mode}</span><small>{data.provider_status?.last_successful_request ? `Last update ${new Date(data.provider_status.last_successful_request).toLocaleString()}` : data.message || 'No successful options request yet'}</small></div>
    <div className="metric-strip"><div><span>UNDERLYING</span><b>{money(data.spot)}</b><small>{data.quote_quality || 'UNAVAILABLE'}</small></div><div><span>IV SUMMARY</span><b>{display(data.iv_rank, '%')}</b></div><div><span>PUT / CALL</span><b>{display(data.put_call_ratio)}</b></div><div><span>DATA</span><Badge tone={dataTone}>{isValidating ? 'REFRESHING' : data.mode}</Badge></div></div>
    <div className="toolbar options-toolbar"><div className="filter-pills"><button className={type === 'ALL' ? 'active' : ''} onClick={() => setType('ALL')}>All</button><button className={type === 'CALL' ? 'active' : ''} onClick={() => setType('CALL')}>Calls</button><button className={type === 'PUT' ? 'active' : ''} onClick={() => setType('PUT')}>Puts</button></div><select className="table-select" value={expiry} onChange={event => setExpiry(event.target.value)}><option value="ALL">All expirations</option>{expirations.map(item => <option value={item} key={item}>{item}</option>)}</select><label className="strike-range"><span>Strike</span><input aria-label="Minimum strike" inputMode="decimal" placeholder="Min" value={minimumStrike} onChange={event => setMinimumStrike(event.target.value)}/><i>–</i><input aria-label="Maximum strike" inputMode="decimal" placeholder="Max" value={maximumStrike} onChange={event => setMaximumStrike(event.target.value)}/></label><button className="btn" onClick={() => void retry()}><RefreshCw/> Refresh</button></div>
    <div className="options-grid"><Panel title={data.symbol + ' Option Chain'} eyebrow={rows.length + ' CONTRACTS · ' + data.mode + ' SOURCE'} className="table-panel">{rows.length ? <><div className="table-scroll"><table><thead><tr>{['Expiry', 'Strike', 'Type', 'Bid', 'Ask', 'Last', 'Volume', 'Open Int.', 'IV', 'Delta', 'Gamma', 'Theta', 'Vega'].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{visibleRows.map((item: Data, index: number) => <tr key={(item.contract || item.expiration) + '-' + item.strike + '-' + item.type + '-' + index}><td>{item.expiration}</td><td><b>{item.strike}</b></td><td><Badge tone={item.type === 'CALL' ? 'good' : 'bad'}>{item.type}</Badge></td><td>{display(item.bid)}</td><td>{display(item.ask)}</td><td>{display(item.last)}</td><td>{display(item.volume)}</td><td>{display(item.open_interest)}</td><td>{display(item.iv, '%')}</td><td>{display(item.delta)}</td><td>{display(item.gamma)}</td><td className="negative">{display(item.theta)}</td><td>{display(item.vega)}</td></tr>)}</tbody></table></div><div className="table-pagination"><span>{pageStart}–{pageEnd} of {rows.length} contracts</span><div><button className="btn" disabled={page === 0} onClick={() => setPage(current => Math.max(0, current - 1))}>Previous</button><b>{page + 1} / {pageCount}</b><button className="btn" disabled={page >= pageCount - 1} onClick={() => setPage(current => Math.min(pageCount - 1, current + 1))}>Next</button></div></div></> : <div className="source-empty"><b>Live options unavailable</b><span>{data.message || 'The configured account/feed did not provide contracts matching these filters. No synthetic contracts are shown.'}</span></div>}</Panel>
      <Panel title="Strategy Lab" eyebrow="DETERMINISTIC PAYOFFS · NOT RECOMMENDATIONS"><div className="strategy-list">{data.strategies?.length ? data.strategies.map((item: Data) => <article key={item.name}><div><b>{item.name}</b><Badge tone="warn">RISK DISCLOSED</Badge></div><div><span>Debit / credit <b>{money(item.debit ?? item.credit)}</b></span><span>Max profit <b className="positive">{item.max_profit === null ? 'Uncapped' : money(item.max_profit)}</b></span><span>Max loss <b className="negative">{money(item.max_loss)}</b></span><span>Breakeven <b>{money(item.breakeven)}</b></span></div><p><ShieldAlert/> {item.risk} Outcomes are not guaranteed.</p></article>) : <p className="panel-note">The connected source did not provide enough quoted contracts for payoff summaries.</p>}</div></Panel></div>
  </>;
}
