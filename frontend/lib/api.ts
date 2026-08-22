import {recordClientTiming} from './performance';

function apiUrl(path: string) {
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `/api${suffix}`;
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const started = typeof performance === 'undefined' ? 0 : performance.now();
  recordClientTiming({kind: 'api_start', path});
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {...init, headers, cache: 'no-store', credentials: 'same-origin'});
  } catch (error) {
    recordClientTiming({kind: 'api_error', path, duration_ms: started ? performance.now() - started : undefined});
    throw error;
  }
  recordClientTiming({
    kind: 'api_response', path, status: response.status, duration_ms: started ? performance.now() - started : undefined,
    cache: response.headers.get('x-marketmind-cache'), server_timing: response.headers.get('server-timing'),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = typeof payload.detail === 'string' ? payload.detail : 'MarketMind request could not be completed.';
    if (response.status === 401 && typeof window !== 'undefined') window.location.assign('/login?reason=session');
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

export const money = (value: number | null | undefined) => typeof value === 'number' && Number.isFinite(value)
  ? new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 2}).format(value)
  : '—';
