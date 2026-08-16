'use client';

import {createBrowserClient} from '@supabase/ssr';

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
