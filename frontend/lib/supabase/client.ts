'use client';

import {createBrowserClient} from '@supabase/ssr';
import {createClient, type AuthError, type User} from '@supabase/supabase-js';

let browserClient: ReturnType<typeof createBrowserClient> | undefined;

export function authConfigured() {
  return Boolean(process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY);
}

export function createSupabaseBrowserClient() {
  if (!authConfigured()) throw new Error('Authentication is not configured.');
  if (!browserClient) {
    browserClient = createBrowserClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
      {auth: {flowType: 'pkce'}},
    );
  }
  return browserClient;
}

export async function consumeInvitationOrGetUser(): Promise<{user: User|null; error: AuthError|null}> {
  if (!authConfigured()) throw new Error('Authentication is not configured.');
  const fragment = typeof window === 'undefined' ? '' : window.location.hash;
  const isInvitationRedirect = fragment.includes('type=invite') && fragment.includes('access_token=');
  if (isInvitationRedirect) {
    // Admin invitations cannot use PKCE: the invitation is accepted in a
    // different browser from the one that issued it. Let supabase-js consume
    // and validate the implicit fragment in memory, then hand the validated
    // session to the normal cookie-backed SSR client. Application code never
    // parses, displays, logs, or persistently stores the raw URL tokens.
    const transient = createClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
      {auth: {flowType: 'implicit', persistSession: false, autoRefreshToken: false, detectSessionInUrl: true}},
    );
    const {data, error} = await transient.auth.getSession();
    if (error || !data.session) return {user: null, error};
    const persisted = await createSupabaseBrowserClient().auth.setSession({
      access_token: data.session.access_token,
      refresh_token: data.session.refresh_token,
    });
    return {user: persisted.data.user, error: persisted.error};
  }
  const current = await createSupabaseBrowserClient().auth.getUser();
  return {user: current.data.user, error: current.error};
}
