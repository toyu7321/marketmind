'use client';

import {FormEvent, useState} from 'react';
import {useRouter, useSearchParams} from 'next/navigation';
import {ArrowRight, LockKeyhole, ShieldCheck} from 'lucide-react';
import {authConfigured, createSupabaseBrowserClient} from '@/lib/supabase/client';

export function AuthLogin() {
  const router = useRouter();
  const search = useSearchParams();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const configured = authConfigured();

  async function signIn(event: FormEvent) {
    event.preventDefault();
    if (!configured || busy) return;
    setBusy(true); setMessage('');
    const {error} = await createSupabaseBrowserClient().auth.signInWithPassword({email, password});
    if (error) setMessage('Unable to sign in. Check your credentials or invitation status.');
    else router.replace(search.get('next')?.startsWith('/') ? search.get('next')! : '/');
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
