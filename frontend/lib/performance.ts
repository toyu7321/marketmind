'use client';

export type ClientTiming = {
  kind: 'navigation_start' | 'api_start' | 'api_response' | 'api_error' | 'render_complete';
  path: string;
  at: number;
  duration_ms?: number;
  status?: number;
  cache?: string | null;
  server_timing?: string | null;
};

declare global {
  interface Window { __marketMindLatency?: ClientTiming[]; }
}

const MAX_SAMPLES = 160;
const navigationStarts = new Map<string, number>();

export function recordClientTiming(event: Omit<ClientTiming, 'at'>) {
  if (typeof window === 'undefined') return;
  const sample: ClientTiming = {...event, at: Date.now()};
  const samples = window.__marketMindLatency || [];
  samples.push(sample);
  if (samples.length > MAX_SAMPLES) samples.splice(0, samples.length - MAX_SAMPLES);
  window.__marketMindLatency = samples;
  if (process.env.NEXT_PUBLIC_PERFORMANCE_DEBUG === 'true') console.debug('[MarketMind latency]', sample);
}

export function markNavigationStart(path: string) {
  if (typeof performance === 'undefined') return;
  navigationStarts.set(path, performance.now());
  recordClientTiming({kind: 'navigation_start', path});
}

/** Record after a paint so the number represents visible route shell completion. */
export function markNavigationRenderComplete(path: string) {
  if (typeof window === 'undefined' || typeof performance === 'undefined') return;
  const started = navigationStarts.get(path);
  if (started === undefined) return;
  window.requestAnimationFrame(() => {
    recordClientTiming({kind: 'render_complete', path, duration_ms: performance.now() - started});
    navigationStarts.delete(path);
  });
}
