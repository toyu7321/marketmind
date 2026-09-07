'use client';

import {FormEvent, useEffect, useMemo, useState} from 'react';
import {useRouter} from 'next/navigation';
import {Check, KeyRound, ShieldCheck} from 'lucide-react';

import {ApiError, api} from '@/lib/api';
import {clearClientDataCache} from '@/lib/data';
import {decideInvitationEntry, invitationErrorMessage, passwordChecks, readInvitationCallbackMarker, validateInvitePassword} from '@/lib/invitation';
import {authConfigured, consumeInvitationOrGetUser, createSupabaseBrowserClient} from '@/lib/supabase/client';

type Onboarding = {status: 'pending'|'accepted'; email: string; role: 'USER'|'ADMIN'; active: boolean};
type View = 'loading'|'form'|'expired'|'invalid';

export function AcceptInvite() {
  const router = useRouter();
  const [view, setView] = useState<View>('loading');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const checks = useMemo(() => passwordChecks(password), [password]);

  useEffect(() => {
    let active = true;
    async function prepare() {
      if (!authConfigured()) { setView('invalid'); return; }
      const marker = readInvitationCallbackMarker(window.location.href);
      const {user, error} = await consumeInvitationOrGetUser();
      // Supabase normally clears a successful fragment. This also removes
      // provider error details from browser history without retaining tokens.
      if (window.location.hash) window.history.replaceState({}, '', `${window.location.pathname}${window.location.search}`);
      if (!active) return;
      if (error || !user) {
        setMessage(invitationErrorMessage(marker));
        const entry = decideInvitationEntry(marker, false);
        setView(entry === 'dashboard' ? 'invalid' : entry);
        return;
      }
      try {
        const onboarding = await api<Onboarding>('/account/onboarding');
        if (!active) return;
        const entry = decideInvitationEntry(marker, true, onboarding.status);
        if (entry === 'dashboard') { router.replace('/'); return; }
        if (!onboarding.active || entry !== 'form') { setView(entry); return; }
        if (!user.email || user.email.toLowerCase() !== onboarding.email.toLowerCase()) {
          setView('invalid');
          return;
        }
        setEmail(onboarding.email);
        setView('form');
      } catch (requestError) {
        if (!active) return;
        if (requestError instanceof ApiError && requestError.status === 410) setView('expired');
        else setView('invalid');
        setMessage(requestError instanceof ApiError && requestError.status === 410
          ? 'This invitation is no longer available. Ask your administrator to send a new invitation.'
          : invitationErrorMessage(marker));
      }
    }
    void prepare();
    return () => { active = false; };
  }, [router]);

  async function activate(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setMessage('');
    const validationError = validateInvitePassword(password, confirmation);
    if (validationError) { setMessage(validationError); return; }
    setBusy(true);
    const supabase = createSupabaseBrowserClient();
    const {data: updated, error} = await supabase.auth.updateUser({password});
    if (error || !updated.user) {
      setMessage('Your password could not be saved. The invitation may have expired; request a new invitation and try again.');
      setBusy(false);
      return;
    }
    const {data: verified, error: identityError} = await supabase.auth.getUser();
    if (identityError || !verified.user || verified.user.id !== updated.user.id || verified.user.email?.toLowerCase() !== email.toLowerCase()) {
      await supabase.auth.signOut({scope: 'local'});
      setMessage('Account identity could not be verified. Please request a new invitation.');
      setBusy(false);
      return;
    }
    try {
      await api<{status: 'accepted'|'already_accepted'}>('/account/onboarding/accept', {method: 'POST'});
      void clearClientDataCache();
      router.replace('/');
      router.refresh();
    } catch {
      await supabase.auth.signOut({scope: 'local'});
      setMessage('Your password was saved, but activation could not be finalized. Sign in with the new password to resume safely.');
      setBusy(false);
    }
  }

  return <main className="auth-page"><section className="auth-card invite-card">
    <div className="auth-mark">{view === 'form' ? <KeyRound/> : <ShieldCheck/>}</div>
    <span className="eyebrow">PRIVATE ACCESS / INVITATION</span>
    <h1>MARKET<span>MIND</span></h1>
    {view === 'loading' && <div className="invite-status" aria-live="polite"><b>Verifying invitation…</b><span>Please keep this page open.</span></div>}
    {view === 'form' && <>
      <div className="invite-heading"><b>Invitation accepted</b><span>Create your password to activate your account.</span></div>
      <div className="invite-email"><span>Email</span><b>{email}</b></div>
      <form className="auth-form" onSubmit={activate}>
        <label>New password<input aria-label="New password" type="password" autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)} required/></label>
        <label>Confirm password<input aria-label="Confirm password" type="password" autoComplete="new-password" value={confirmation} onChange={event => setConfirmation(event.target.value)} required/></label>
        <div className="password-rules" aria-label="Password requirements">{checks.map(check => <span className={check.passed ? 'passed' : ''} key={check.label}><Check/>{check.label}</span>)}</div>
        {message && <div className="auth-alert" role="alert"><span>{message}</span></div>}
        <button className="btn primary" disabled={busy}><ShieldCheck/>{busy ? 'Activating…' : 'Activate Account'}</button>
      </form>
    </>}
    {(view === 'expired' || view === 'invalid') && <div className="invite-status error" role="alert"><b>{view === 'expired' ? 'Invitation expired' : 'Invitation unavailable'}</b><span>{message || 'A valid invitation session is required to activate an account.'}</span><a className="btn" href="/login">Return to Sign In</a></div>}
    <small>Public registration is disabled. Only an invitation issued by a MarketMind administrator can activate an account.</small>
  </section></main>;
}
