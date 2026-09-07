'use client';

import {FormEvent, useEffect, useState} from 'react';
import {KeyRound, Laptop, LogOut, RefreshCw, ShieldAlert, ShieldCheck, Smartphone} from 'lucide-react';
import Image from 'next/image';
import {api} from '@/lib/api';
import {clearClientDataCache} from '@/lib/data';
import {createTotpQrCode, extractTotpSetupKey, MARKETMIND_TOTP_FACTOR_NAME, maskedTotpSetupKey, planTotpSetup} from '@/lib/mfa';
import type {MfaFactorSummary} from '@/lib/mfa';
import {authConfigured, createSupabaseBrowserClient} from '@/lib/supabase/client';

type Account = {user:{email:string;display_name:string;role:string};security:{mfa_level:string;mfa_required_for_admin:boolean;active_sessions:number;user_kill_switch:boolean};broker_connection:string;paper_trading:string;live_trading:string};
type Session = {id:string;current:boolean;created_at:string;last_seen_at:string;revoked_at:string|null;device:string};

export function SecurityConsole() {
  const [account, setAccount] = useState<Account>(); const [sessions, setSessions] = useState<Session[]>([]);
  const [factorId, setFactorId] = useState(''); const [totpUri, setTotpUri] = useState(''); const [qrCodeDataUrl, setQrCodeDataUrl] = useState(''); const [showManualSetup, setShowManualSetup] = useState(false); const [manualSetupKey, setManualSetupKey] = useState(''); const [revealManualKey, setRevealManualKey] = useState(false); const [code, setCode] = useState(''); const [message, setMessage] = useState('');
  const [mfaLoading, setMfaLoading] = useState(() => authConfigured());
  const refresh = () => { api<Account>('/account').then(setAccount).catch(() => undefined); api<{sessions:Session[]}>('/account/sessions').then(data => setSessions(data.sessions)).catch(() => undefined); };
  useEffect(refresh, []);
  useEffect(() => {
    if (!authConfigured()) { setMfaLoading(false); return; }
    let active = true;
    void createSupabaseBrowserClient().auth.mfa.listFactors()
      .then(({data, error}: {data: {all: MfaFactorSummary[]} | null; error: unknown}) => {
        if (!active) return;
        if (error || !data) { setMessage('MFA status could not be loaded.'); return; }
        const plan = planTotpSetup(data.all);
        if (plan.kind === 'challenge') setFactorId(plan.factorId);
      })
      .catch(() => { if (active) setMessage('MFA status could not be loaded.'); })
      .finally(() => { if (active) setMfaLoading(false); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    let cancelled = false;
    if (!totpUri) { setQrCodeDataUrl(''); return; }
    void createTotpQrCode(totpUri)
      .then(dataUrl => { if (!cancelled) setQrCodeDataUrl(dataUrl); })
      .catch(() => { if (!cancelled) { setQrCodeDataUrl(''); setShowManualSetup(true); setMessage('The QR code could not be generated locally. Use the manual setup key instead.'); } });
    return () => { cancelled = true; };
  }, [totpUri]);
  async function startMfa() {
    if (!authConfigured() || mfaLoading) return; setMessage(''); setMfaLoading(true);
    const client = createSupabaseBrowserClient();
    try {
      const listed = await client.auth.mfa.listFactors();
      if (listed.error || !listed.data) { setMessage('MFA status could not be loaded.'); return; }
      const plan = planTotpSetup(listed.data.all);
      if (plan.kind === 'challenge') {
        setFactorId(plan.factorId); setTotpUri('');
        setMessage('Enter the six-digit code from your existing authenticator to verify this session.');
        return;
      }
      if (plan.kind === 'restart') {
        for (const staleFactorId of plan.staleFactorIds) {
          const removed = await client.auth.mfa.unenroll({factorId: staleFactorId});
          if (removed.error) { setMessage('The incomplete MFA enrollment could not be restarted.'); return; }
        }
      }
      const {data, error} = await client.auth.mfa.enroll({factorType:'totp', friendlyName:MARKETMIND_TOTP_FACTOR_NAME});
      if (error || !data) { setMessage('MFA enrollment could not be started.'); return; }
      setFactorId(data.id); setTotpUri(data.totp.uri); setShowManualSetup(false); setManualSetupKey(''); setRevealManualKey(false); setMessage('Scan the QR code, then enter the six-digit code.');
    } catch { setMessage('MFA enrollment could not be started.'); }
    finally { setMfaLoading(false); }
  }
  async function verify(event: FormEvent) {
    event.preventDefault(); if (!authConfigured() || !factorId) return;
    const client = createSupabaseBrowserClient();
    const result = await client.auth.mfa.challengeAndVerify({factorId, code});
    setMessage(result.error ? 'That verification code was not accepted.' : 'MFA is enabled for this session.');
    if (!result.error) { setTotpUri(''); setShowManualSetup(false); setManualSetupKey(''); setRevealManualKey(false); setCode(''); await clearClientDataCache(); refresh(); }
  }
  function toggleManualSetup() { if (showManualSetup) { setShowManualSetup(false); setManualSetupKey(''); setRevealManualKey(false); } else { setManualSetupKey(extractTotpSetupKey(totpUri)); setShowManualSetup(true); } }
  async function revokeOthers() { try { await api('/account/sessions/revoke', {method:'POST', body:JSON.stringify({all_other_sessions:true})}); setMessage('Other sessions were revoked.'); refresh(); } catch { setMessage('Step-up authentication is required to revoke sessions.'); } }
  async function signOut() { try { await api('/account/logout', {method:'POST'}); } catch {} void clearClientDataCache(); if (authConfigured()) await createSupabaseBrowserClient().auth.signOut(); window.location.assign('/login'); }
  return <><div className="page-heading"><div><span className="eyebrow">ACCOUNT / DEFENSE IN DEPTH</span><h1>Security</h1><p>Session visibility, multi-factor protection, and broker safety controls.</p></div><div className="heading-actions"><button className="btn" onClick={refresh}><RefreshCw/>Refresh</button><button className="btn" onClick={signOut}><LogOut/>Sign out</button></div></div>
    <div className="two-col security-grid"><section className="panel"><div className="panel-head"><div><span className="eyebrow">IDENTITY</span><h2>{account?.user.display_name || account?.user.email || 'Secure account'}</h2></div><span className="badge good"><ShieldCheck/>PRIVATE</span></div><div className="security-status"><div><span>MFA</span><b>{account?.security.mfa_level === 'aal2' ? 'Enabled' : 'Not verified'}</b></div><div><span>Active sessions</span><b>{account?.security.active_sessions ?? '—'}</b></div><div><span>Broker connection</span><b>{account?.broker_connection || 'Not Connected'}</b></div><div><span>Live trading</span><b className="negative">{account?.live_trading || 'Locked'}</b></div></div>{account?.security.mfa_required_for_admin && account.security.mfa_level !== 'aal2' && <div className="invalidation"><ShieldAlert/><span><small>ADMIN MFA REQUIRED</small>Verify an authenticator for this session, enrolling one first if needed, before using administrator actions.</span></div>}</section>
    <section className="panel"><div className="panel-head"><div><span className="eyebrow">AUTHENTICATOR APP</span><h2>Multi-factor authentication</h2></div><KeyRound/></div>{account?.security.mfa_level === 'aal2' ? <p className="panel-note">This session has verified multi-factor authentication.</p> : !factorId ? <button className="btn primary" onClick={startMfa} disabled={!authConfigured() || mfaLoading}><Smartphone/>{mfaLoading ? 'Checking authenticator…' : 'Set up authenticator'}</button> : <form onSubmit={verify} className="auth-form compact">{totpUri ? <div className="mfa-enrollment"><div className="mfa-qr">{qrCodeDataUrl ? <Image src={qrCodeDataUrl} alt="Authenticator setup QR code" width={256} height={256} unoptimized draggable={false}/> : <span>Generating QR code…</span>}</div><div className="mfa-enrollment-copy"><b>Scan this QR code with your authenticator app.</b><p className="mfa-warning">Treat this QR code and setup key like a password. Anyone who has it can generate your MFA codes.</p><button type="button" className="btn" onClick={toggleManualSetup}>{showManualSetup ? 'Hide manual setup key' : 'Show manual setup key'}</button>{showManualSetup && <div className="mfa-manual-setup"><span>Manual setup key</span><code>{revealManualKey ? manualSetupKey || 'Setup key unavailable' : manualSetupKey ? maskedTotpSetupKey() : 'Setup key unavailable'}</code><button type="button" className="btn" onClick={() => setRevealManualKey(value => !value)} disabled={!manualSetupKey}>{revealManualKey ? 'Mask setup key' : 'Reveal setup key'}</button></div>}</div></div> : <p className="panel-note">Enter a code from your existing authenticator to verify this session.</p>}<label>Verification code<input inputMode="numeric" pattern="[0-9]{6}" value={code} onChange={event => setCode(event.target.value)} required/></label><button className="btn primary">Verify MFA</button></form>}<p className="panel-note">Use a TOTP-capable authenticator. MarketMind never receives or stores the authenticator secret.</p></section>
    <section className="panel wide"><div className="panel-head"><div><span className="eyebrow">ACTIVE SESSIONS</span><h2>Devices</h2></div><button className="btn" onClick={revokeOthers}>Sign out all other sessions</button></div><div className="session-list">{sessions.map(session => <div key={session.id}><Laptop/><span><b>{session.current ? 'Current session' : 'Session'}</b><small>{session.device} · Last active {new Date(session.last_seen_at).toLocaleString()}</small></span><em className={session.revoked_at ? 'negative' : 'positive'}>{session.revoked_at ? 'REVOKED' : 'ACTIVE'}</em></div>)}{!sessions.length && <p className="panel-note">No active session metadata has been recorded yet.</p>}</div></section></div>{message && <div className="save-message">{message}</div>}</>;
}
