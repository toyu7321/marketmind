'use client';

import {FormEvent, useEffect, useState} from 'react';
import {useRouter, useSearchParams} from 'next/navigation';
import {ArrowRight, LockKeyhole, ShieldCheck} from 'lucide-react';
import {clearClientDataCache} from '@/lib/data';
import {ApiError, api} from '@/lib/api';
import {authConfigured, consumeInvitationOrGetUser, createSupabaseBrowserClient} from '@/lib/supabase/client';

export function AuthLogin() {
  const router = useRouter();
  const search = useSearchParams();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const configured = authConfigured();

  async function continueAfterAuthentication() {
    void clearClientDataCache();
    try {
      const onboarding = await api<{status: 'pending'|'accepted'}>('/account/onboarding');
      if (onboarding.status === 'pending') {
        router.replace('/auth/accept-invite?resume=1');
        return;
      }
    } catch (error) {
      // Accounts created before invitation tracking (including the bootstrap
      // administrator) correctly have no onboarding record.
      if (!(error instanceof ApiError && error.status === 404)) throw error;
    }
    const next = search.get('next');
    router.replace(next?.startsWith('/') && !next.startsWith('//') ? next : '/');
  }

  useEffect(() => {
    if (!configured) return;
    let active = true;
    void (async () => {
      const result = await consumeInvitationOrGetUser();
      if (!active || result.error || !result.user) return;
      try { await continueAfterAuthentication(); } catch { if (active) setMessage('Your session could not be prepared. Please try again.'); }
    })();
    return () => { active = false; };
  // The router and search object are stable for this mounted login screen.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [configured]);

  async function signIn(event: FormEvent) {
    event.preventDefault();
    if (!configured || busy) return;
    setBusy(true); setMessage('');
    const {error} = await createSupabaseBrowserClient().auth.signInWithPassword({email, password});
    if (error) setMessage('Unable to sign in. Check your credentials or invitation status.');
    else {
      try { await continueAfterAuthentication(); }
      catch { setMessage('Signed in, but account setup could not be checked. Please try again.'); }
    }
    setBusy(false);
  }

  return <main className="auth-page"><section className="auth-card">
    <div className="auth-mark"><ShieldCheck/></div><span className="eyebrow">PRIVATE ACCESS / INVITE ONLY</span>
    <h1>MARKET<span>MIND</span></h1><p>AI Market Intelligence Terminal</p>
    {!configured ? <div className="auth-alert"><b>Authentication setup required</b><span>This deployment is intentionally locked until its secure identity provider is configured.</span></div> : <form onSubmit={signIn} className="auth-form">
      <label>Email<input aria-label="Email" type="email" autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} required/></label>
      <label>Password<input aria-label="Password" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} required/></label>
      {message && <div className="auth-alert"><span>{message}</span></div>}
      <button className="btn primary" disabled={busy}><LockKeyhole/>{busy ? 'Signing in…' : 'Sign In'}<ArrowRight/></button>
    </form>}
    <small>Access is invitation-only. Contact your administrator if you need an account.</small>
  </section></main>;
}
