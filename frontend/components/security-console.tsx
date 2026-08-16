'use client';

import {FormEvent, useEffect, useState} from 'react';
import {KeyRound, Laptop, LogOut, RefreshCw, ShieldAlert, ShieldCheck, Smartphone} from 'lucide-react';
import {api} from '@/lib/api';
import {authConfigured, createSupabaseBrowserClient} from '@/lib/supabase/client';

type Account = {user:{email:string;display_name:string;role:string};security:{mfa_level:string;mfa_required_for_admin:boolean;active_sessions:number;user_kill_switch:boolean};broker_connection:string;paper_trading:string;live_trading:string};
type Session = {id:string;current:boolean;created_at:string;last_seen_at:string;revoked_at:string|null;device:string};

export function SecurityConsole() {
  const [account, setAccount] = useState<Account>(); const [sessions, setSessions] = useState<Session[]>([]);
  const [factorId, setFactorId] = useState(''); const [totpUri, setTotpUri] = useState(''); const [code, setCode] = useState(''); const [message, setMessage] = useState('');
  const refresh = () => { api<Account>('/account').then(setAccount).catch(() => undefined); api<{sessions:Session[]}>('/account/sessions').then(data => setSessions(data.sessions)).catch(() => undefined); };
  useEffect(refresh, []);
  async function enroll() {
    if (!authConfigured()) return; setMessage('');
    const {data, error} = await createSupabaseBrowserClient().auth.mfa.enroll({factorType:'totp', friendlyName:'MarketMind authenticator'});
    if (error || !data) { setMessage('MFA enrollment could not be started.'); return; }
    setFactorId(data.id); setTotpUri(data.totp.uri); setMessage('Scan the authenticator URI, then enter the six-digit code.');
  }
  async function verify(event: FormEvent) {
    event.preventDefault(); if (!authConfigured() || !factorId) return;
    const client = createSupabaseBrowserClient(); const challenge = await client.auth.mfa.challenge({factorId});
    if (challenge.error || !challenge.data) { setMessage('MFA challenge failed.'); return; }
    const result = await client.auth.mfa.verify({factorId, challengeId:challenge.data.id, code});
    setMessage(result.error ? 'That verification code was not accepted.' : 'MFA is enabled for this session.');
    if (!result.error) { setTotpUri(''); setCode(''); refresh(); }
  }
  async function revokeOthers() { try { await api('/account/sessions/revoke', {method:'POST', body:JSON.stringify({all_other_sessions:true})}); setMessage('Other sessions were revoked.'); refresh(); } catch { setMessage('Step-up authentication is required to revoke sessions.'); } }
  async function signOut() { try { await api('/account/logout', {method:'POST'}); } catch {} if (authConfigured()) await createSupabaseBrowserClient().auth.signOut(); window.location.assign('/login'); }
  return <><div className="page-heading"><div><span className="eyebrow">ACCOUNT / DEFENSE IN DEPTH</span><h1>Security</h1><p>Session visibility, multi-factor protection, and broker safety controls.</p></div><div className="heading-actions"><button className="btn" onClick={refresh}><RefreshCw/>Refresh</button><button className="btn" onClick={signOut}><LogOut/>Sign out</button></div></div>
    <div className="two-col security-grid"><section className="panel"><div className="panel-head"><div><span className="eyebrow">IDENTITY</span><h2>{account?.user.display_name || account?.user.email || 'Secure account'}</h2></div><span className="badge good"><ShieldCheck/>PRIVATE</span></div><div className="security-status"><div><span>MFA</span><b>{account?.security.mfa_level === 'aal2' ? 'Enabled' : 'Not verified'}</b></div><div><span>Active sessions</span><b>{account?.security.active_sessions ?? '—'}</b></div><div><span>Broker connection</span><b>{account?.broker_connection || 'Not Connected'}</b></div><div><span>Live trading</span><b className="negative">{account?.live_trading || 'Locked'}</b></div></div>{account?.security.mfa_required_for_admin && account.security.mfa_level !== 'aal2' && <div className="invalidation"><ShieldAlert/><span><small>ADMIN MFA REQUIRED</small>Enroll and verify an authenticator before using administrator actions.</span></div>}</section>
    <section className="panel"><div className="panel-head"><div><span className="eyebrow">AUTHENTICATOR APP</span><h2>Multi-factor authentication</h2></div><KeyRound/></div>{!totpUri ? <button className="btn primary" onClick={enroll} disabled={!authConfigured()}><Smartphone/>Set up authenticator</button> : <form onSubmit={verify} className="auth-form compact"><label>Authenticator URI<code>{totpUri}</code></label><label>Verification code<input inputMode="numeric" pattern="[0-9]{6}" value={code} onChange={event => setCode(event.target.value)} required/></label><button className="btn primary">Verify MFA</button></form>}<p className="panel-note">Use a TOTP-capable authenticator. MarketMind never receives or stores the authenticator secret.</p></section>
    <section className="panel wide"><div className="panel-head"><div><span className="eyebrow">ACTIVE SESSIONS</span><h2>Devices</h2></div><button className="btn" onClick={revokeOthers}>Sign out all other sessions</button></div><div className="session-list">{sessions.map(session => <div key={session.id}><Laptop/><span><b>{session.current ? 'Current session' : 'Session'}</b><small>{session.device} · Last active {new Date(session.last_seen_at).toLocaleString()}</small></span><em className={session.revoked_at ? 'negative' : 'positive'}>{session.revoked_at ? 'REVOKED' : 'ACTIVE'}</em></div>)}{!sessions.length && <p className="panel-note">No active session metadata has been recorded yet.</p>}</div></section></div>{message && <div className="save-message">{message}</div>}</>;
}
