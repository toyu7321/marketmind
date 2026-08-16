'use client';

import {useEffect, useState} from 'react';
import {Check, CloudOff, ShieldAlert} from 'lucide-react';
import {api} from '@/lib/api';
import {Badge, ErrorState, Panel, Skeleton} from './ui';

type Config = Record<string, any>;

export function SettingsConsole() {
  const [data, setData] = useState<Config>();
  const [draft, setDraft] = useState<Config>();
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const load = () => api<Config>('/settings').then(value => {
    setData(value);
    setDraft(JSON.parse(JSON.stringify(value)));
  }).catch(() => setMessage('Could not reach the local API.'));

  useEffect(() => { void load(); }, []);
  if (!data || !draft) return message ? <ErrorState retry={load}/> : <Skeleton/>;

  const setNested = (group: string, key: string, value: any) => setDraft((current: Config) => ({...current, [group]: {...current[group], [key]: value}}));
  const weightTotal = (weights: Config) => Object.values(weights).reduce((total: number, value) => total + Number(value), 0);
  const save = async () => {
    setSaving(true);
    setMessage('');
    try {
      const payload = {
        timezone: draft.timezone, refresh_seconds: draft.refresh_seconds, watchlist: draft.watchlist,
        market_weights: draft.market_weights, stock_weights: draft.stock_weights, risk: draft.risk,
        automation: draft.automation, ai: draft.ai,
      };
      const saved = await api<Config>('/settings', {method: 'PUT', body: JSON.stringify(payload)});
      setData({...data, ...saved.settings});
      setDraft({...draft, ...saved.settings});
      setMessage('Saved locally. New score and risk settings apply to future requests.');
    } catch {
      setMessage('Save failed. Score weights must each total 100 and the backend must be running.');
    } finally {
      setSaving(false);
    }
  };

  return <><div className="page-heading"><div><span className="eyebrow">SYSTEM CONTROL / LOCAL CONFIGURATION</span><h1>Settings & Diagnostics</h1><p>Provider state, editable score weights, automation, and hard risk constraints.</p></div><div className="heading-actions"><button className="btn primary" onClick={save} disabled={saving}><Check/>{saving ? 'Saving…' : 'Save changes'}</button></div></div>
    {message && <p className={message.startsWith('Saved') ? 'save-message positive' : 'save-message negative'}>{message}</p>}
    <div className="settings-layout"><nav>{['General', 'Watchlist', 'Scoring', 'AI Analysis', 'Providers', 'Broker', 'Risk Controls', 'Automation'].map((label, index) => <button className={index === 0 ? 'active' : ''} key={label}>{label}</button>)}</nav><div>
      <Panel title="Provider Diagnostics" eyebrow="CREDENTIALS REMAIN SERVER-SIDE"><div className="provider-list">{Object.entries(data.providers).map(([key, value]) => {
        const label = String(value);
        const connected = label.includes('Connected') || label.includes('configured');
        return <div key={key}><span><i className={label.includes('Disabled') ? 'off' : connected ? '' : 'warn'}/><b>{key}</b></span><Badge tone={connected ? 'good' : label.includes('Disabled') ? 'neutral' : 'warn'}>{label}</Badge></div>;
      })}</div><p className="panel-note"><CloudOff/> Add keys only to the root .env file. The browser never receives secrets; a failed provider returns explicitly labeled demo data.</p></Panel>
      <Panel title="General & AI" eyebrow="TERMINAL BEHAVIOR"><div className="form-grid"><label>Timezone<select value={draft.timezone} onChange={event => setDraft({...draft, timezone: event.target.value})}><option>America/New_York</option><option>UTC</option><option>Australia/Sydney</option></select></label><label>Refresh interval<select value={draft.refresh_seconds} onChange={event => setDraft({...draft, refresh_seconds: Number(event.target.value)})}><option value="30">30 seconds</option><option value="60">60 seconds</option><option value="300">5 minutes</option></select></label><label>AI model<input value={String(draft.ai.model)} readOnly/></label><label>AI analysis scope<select value={String(draft.ai.mode)} onChange={event => setNested('ai', 'mode', event.target.value)}><option>Manual</option><option>Top 5 only</option><option>Top 10 only</option><option>Watchlist only</option></select></label></div></Panel>
      <Panel title="Watchlist" eyebrow="SYMBOLS · MAXIMUM 50"><label className="watchlist-editor">Symbols (comma separated)<input value={draft.watchlist.join(', ')} onChange={event => setDraft({...draft, watchlist: event.target.value.split(',').map(value => value.trim().toUpperCase()).filter(Boolean)})}/></label></Panel>
      <Panel title="Market Score Weights" eyebrow="MUST TOTAL 100"><div className="weight-grid">{Object.entries(draft.market_weights).map(([key, value]) => <label key={key}><span>{key}</span><input type="number" value={Number(value)} onChange={event => setNested('market_weights', key, Number(event.target.value))}/></label>)}</div><p className="weight-total">Current total: {weightTotal(draft.market_weights)}</p></Panel>
      <Panel title="Stock Score Weights" eyebrow="MUST TOTAL 100"><div className="weight-grid">{Object.entries(draft.stock_weights).map(([key, value]) => <label key={key}><span>{key}</span><input type="number" value={Number(value)} onChange={event => setNested('stock_weights', key, Number(event.target.value))}/></label>)}</div><p className="weight-total">Current total: {weightTotal(draft.stock_weights)}</p></Panel>
      <Panel title="Risk Authority" eyebrow="AI AND STRATEGY CANNOT BYPASS"><div className="form-grid">{Object.entries(draft.risk).filter(([key]) => key !== 'kill_switch').map(([key, value]) => <label key={key}>{key.replaceAll('_', ' ')}<input type="number" value={Number(value)} onChange={event => setNested('risk', key, Number(event.target.value))}/></label>)}</div><button className={'kill-switch' + (draft.risk.kill_switch ? ' armed' : '')} onClick={() => setNested('risk', 'kill_switch', !draft.risk.kill_switch)}><ShieldAlert/> {draft.risk.kill_switch ? 'KILL SWITCH ACTIVE' : 'ARM TRADING KILL SWITCH'}<small>{draft.risk.kill_switch ? 'New orders will be rejected once saved.' : 'Blocks new paper and proposed live orders once saved.'}</small></button></Panel>
      <Panel title="Automation Schedule" eyebrow="RATE-LIMIT AWARE JOBS"><div className="toggles">{Object.entries(draft.automation).map(([key, value]) => <label key={key}><span><b>{key.replace('market', 'Market ')}</b><small>{key === 'premarket' ? 'Morning brief, scan, sectors' : key === 'session' ? 'Quotes, signals, watchlist' : 'Snapshots, evaluations, summaries'}</small></span><input type="checkbox" checked={Boolean(value)} onChange={event => setNested('automation', key, event.target.checked)}/></label>)}</div></Panel>
    </div></div>
  </>;
}
