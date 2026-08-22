'use client';

import {useEffect, useState} from 'react';
import useSWR, {mutate as mutateAll} from 'swr';

import {api, ApiError} from './api';
import {authConfigured, createSupabaseBrowserClient} from './supabase/client';

type CacheScope = 'market' | 'session';

export type DataPolicy = {
  scope: CacheScope;
  dedupingInterval: number;
  refreshInterval: number;
};

const SECOND = 1_000;
const SESSION_POLICY: DataPolicy = {scope: 'session', dedupingInterval: 20 * SECOND, refreshInterval: 30 * SECOND};

/**
 * This is an in-memory browser cache only. It never writes API payloads to the
 * service worker, localStorage, or an HTTP cache. Session-keyed entries keep
 * account-specific watchlists, holdings, settings, and administration data
 * isolated if a person signs out and another person uses the same browser.
 */
export function dataPolicyForPath(path: string): DataPolicy {
  if (path === '/health') return {scope: 'market', dedupingInterval: 45 * SECOND, refreshInterval: 60 * SECOND};
  if (path.startsWith('/options/')) return {scope: 'market', dedupingInterval: 15 * SECOND, refreshInterval: 20 * SECOND};
  if (path.startsWith('/stocks/')) return {scope: 'session', dedupingInterval: 60 * SECOND, refreshInterval: 90 * SECOND};
  if (path === '/news') return {scope: 'session', dedupingInterval: 30 * SECOND, refreshInterval: 45 * SECOND};
  if (path === '/dashboard' || path === '/scanner') return {scope: 'session', dedupingInterval: 15 * SECOND, refreshInterval: 20 * SECOND};
  if (path === '/portfolio') return {scope: 'session', dedupingInterval: 15 * SECOND, refreshInterval: 30 * SECOND};
  if (path === '/settings' || path === '/account') return {scope: 'session', dedupingInterval: 30 * SECOND, refreshInterval: 60 * SECOND};
  return SESSION_POLICY;
}

export function cacheKeyForPath(scope: string | null, path: string, policy = dataPolicyForPath(path)) {
  if (policy.scope === 'market') return ['market', path] as const;
  return scope ? ['session', scope, path] as const : null;
}

let knownSessionScope: string | null | undefined;
let sessionScopeRequest: Promise<string | null> | undefined;

async function loadSessionScope(): Promise<string | null> {
  if (knownSessionScope !== undefined) return knownSessionScope;
  if (!sessionScopeRequest) {
    const request: Promise<string | null> = !authConfigured()
      ? Promise.resolve(null)
      : createSupabaseBrowserClient().auth.getUser()
        .then((result: {data: {user: {id: string} | null}}) => result.data.user?.id || null)
        .catch(() => null);
    sessionScopeRequest = request.then(value => {
          knownSessionScope = value;
          return value;
        });
  }
  return sessionScopeRequest;
}

function useSessionScope(needsSession: boolean) {
  const [scope, setScope] = useState<string | null>(() => needsSession ? knownSessionScope ?? null : 'market');

  useEffect(() => {
    let active = true;
    if (!needsSession) {
      setScope('market');
      return () => { active = false; };
    }
    void loadSessionScope().then(value => { if (active) setScope(value); });
    return () => { active = false; };
  }, [needsSession]);

  return scope;
}

export function useApiData<T>(path: string, override?: Partial<DataPolicy>) {
  const policy = {...dataPolicyForPath(path), ...override};
  const sessionScope = useSessionScope(policy.scope === 'session');
  const key = cacheKeyForPath(sessionScope, path, policy);
  const response = useSWR<T, ApiError>(key, () => api<T>(path), {
    dedupingInterval: policy.dedupingInterval,
    refreshInterval: policy.refreshInterval,
    focusThrottleInterval: policy.dedupingInterval,
    revalidateOnFocus: true,
    revalidateOnReconnect: true,
    revalidateIfStale: true,
    keepPreviousData: true,
  });
  const [waking, setWaking] = useState(false);

  useEffect(() => {
    if (!response.isLoading) {
      setWaking(false);
      return;
    }
    const timeout = window.setTimeout(() => setWaking(true), 900);
    return () => window.clearTimeout(timeout);
  }, [response.isLoading]);

  return {...response, waking, retry: () => response.mutate()};
}

/** Clear all memory-only SWR entries at an authentication boundary. */
export function clearClientDataCache() {
  knownSessionScope = undefined;
  sessionScopeRequest = undefined;
  return mutateAll(() => true, undefined, {revalidate: false});
}
