'use client';

import {useEffect, useState} from 'react';
import {ShieldAlert, Zap} from 'lucide-react';
import {api} from '@/lib/api';
import {BigChart, Panel, Skeleton} from './ui';

type Data = Record<string, any>;

export function BacktestWorkbench() {
  const [form, setForm] = useState({ticker: 'SPY', days: 500, score_threshold: 65, transaction_cost_bps: 5});
  const [result, setResult] = useState<Data>();
  const [failure, setFailure] = useState('');
  const [running, setRunning] = useState(false);
  const update = (key: string, value: string) => setForm(current => ({...current, [key]: key === 'ticker' ? value.toUpperCase() : Number(value)}));
  const run = async () => {
    setRunning(true); setFailure('');
    try {
      setResult(await api<Data>('/backtest', {method: 'POST', body: JSON.stringify(form)}));
    } catch {
      setFailure('Backtest failed. Check the backend health and requested inputs.');
    } finally {
      setRunning(false);
    }
  };
  // The initial research run is deliberately performed once with default inputs.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void run(); }, []);

  return <><div className="page-heading"><div><span className="eyebrow">RESEARCH LAB / NO LOOK-AHEAD</span><h1>Strategy Backtest</h1><p>Signals form at one close and execute on the following session. Transaction costs are deducted.</p></div><div className="heading-actions"><button className="btn primary" onClick={run} disabled={running}><Zap/>{running ? 'Running…' : 'Run backtest'}</button></div></div>
    <Panel className="backtest-form"><div className="form-grid"><label>Ticker<input value={form.ticker} onChange={event => update('ticker', event.target.value)}/></label><label>History (sessions)<input type="number" min="30" max="2000" value={form.days} onChange={event => update('days', event.target.value)}/></label><label>Strategy<select defaultValue="score"><option value="score">Stock Score Threshold</option></select></label><label>Score threshold<input type="number" min="0" max="100" value={form.score_threshold} onChange={event => update('score_threshold', event.target.value)}/></label><label>Execution<select defaultValue="next"><option value="next">Next session</option></select></label><label>Transaction cost (bps)<input type="number" min="0" value={form.transaction_cost_bps} onChange={event => update('transaction_cost_bps', event.target.value)}/></label></div></Panel>
    {failure && <div className="error-state"><b>{failure}</b><button onClick={run}>Retry</button></div>}
    {!result && !failure && <Skeleton/>}
    {result && <><div className="metric-strip compact">{[['TOTAL RETURN', result.total_return + '%'], ['CAGR', result.cagr + '%'], ['SHARPE', result.sharpe], ['SORTINO', result.sortino], ['MAX DRAWDOWN', result.max_drawdown + '%'], ['WIN RATE', result.win_rate + '%'], ['PROFIT FACTOR', result.profit_factor], ['EXPOSURE', result.exposure + '%']].map(([key, value]) => <div key={String(key)}><span>{key}</span><b>{String(value)}</b></div>)}</div><div className="two-col"><Panel title="Equity Curve" eyebrow={'MARKETMIND · BENCHMARK ' + result.benchmark_return + '%'}><BigChart data={result.equity_curve.map((item: Data) => ({v: item.value, time: item.date}))} secondary={result.benchmark_curve.map((item: Data) => item.value)}/></Panel><Panel title="Drawdown" eyebrow="UNDERWATER CURVE"><BigChart color="#ff6685" data={result.drawdown.map((item: Data) => ({v: item.value, time: item.date}))}/></Panel><Panel title="Trade History" eyebrow={result.trades + ' OPEN / CLOSED TRADES'} className="table-panel"><div className="table-scroll"><table><thead><tr>{['Entry', 'Exit', 'Entry Px', 'Exit Px', 'Return', 'State'].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{result.trade_history.map((item: Data, index: number) => <tr key={index}><td>{item.entry_date}</td><td>{item.exit_date}</td><td>{item.entry_price}</td><td>{item.exit_price}</td><td className={item.return_percent >= 0 ? 'positive' : 'negative'}>{item.return_percent}%</td><td>{item.status}</td></tr>)}</tbody></table></div></Panel><Panel title="Research Assumptions" eyebrow="LIMITATIONS"><p className="panel-note">Signals execute {result.assumptions.signal_execution}; assumed transaction costs are {result.assumptions.transaction_cost_bps} bps. Use licensed point-in-time data before drawing investment conclusions.</p></Panel></div></>}
    <p className="disclaimer"><ShieldAlert/> {result?.mode === 'DEMO' ? 'Demo provider history is synthetic. ' : ''}Backtests do not model taxes, corporate actions, partial fills, borrowing costs, or survivorship bias.</p>
  </>;
}
