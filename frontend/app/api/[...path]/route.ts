import {NextRequest, NextResponse} from 'next/server';
import {authServerConfigured, createSupabaseServerClient} from '@/lib/supabase/server';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

function backendUrl() {
  return process.env.BACKEND_URL?.trim().replace(/\/+$/, '');
}

async function proxy(request: NextRequest, context: {params: Promise<{path: string[]}>}) {
  const startedAt = performance.now();
  const {path} = await context.params;
  const targetBase = backendUrl();
  if (!targetBase) return NextResponse.json({detail: 'Service temporarily unavailable.'}, {status: 503, headers: {'Cache-Control': 'no-store'}});
  const endpoint = path.join('/');
  const target = new URL(`/api/${endpoint}`, `${targetBase}/`);
  target.search = request.nextUrl.search;
  const isHealth = endpoint === 'health';
  const method = request.method.toUpperCase();
  if (!isHealth && !authServerConfigured()) return NextResponse.json({detail: 'Authentication is not configured.'}, {status: 503, headers: {'Cache-Control': 'no-store'}});
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const origin = request.headers.get('origin');
    if (!origin || origin !== request.nextUrl.origin) return NextResponse.json({detail: 'Invalid request origin.'}, {status: 403, headers: {'Cache-Control': 'no-store'}});
  }
  let accessToken = '';
  let authDuration = 0;
  if (!isHealth) {
    const authStartedAt = performance.now();
    const supabase = await createSupabaseServerClient();
    const [{data: userData, error: userError}, {data: sessionData}] = await Promise.all([
      supabase.auth.getUser(), supabase.auth.getSession(),
    ]);
    authDuration = performance.now() - authStartedAt;
    if (userError || !userData.user) return NextResponse.json({detail: 'Authentication is required.'}, {status: 401, headers: {'Cache-Control': 'no-store'}});
    accessToken = sessionData.session?.access_token || '';
    if (!accessToken) return NextResponse.json({detail: 'Authentication is required.'}, {status: 401, headers: {'Cache-Control': 'no-store'}});
  }
  const headers = new Headers();
  const contentType = request.headers.get('content-type');
  const idempotencyKey = request.headers.get('idempotency-key');
  if (contentType) headers.set('content-type', contentType);
  if (idempotencyKey) headers.set('idempotency-key', idempotencyKey);
  headers.set('accept', 'application/json');
  if (accessToken) headers.set('authorization', `Bearer ${accessToken}`);
  try {
    const backendStartedAt = performance.now();
    const upstream = await fetch(target, {method, headers, body: ['GET', 'HEAD'].includes(method) ? undefined : await request.arrayBuffer(), cache: 'no-store', redirect: 'error'});
    const backendDuration = performance.now() - backendStartedAt;
    const responseHeaders = new Headers();
    const upstreamType = upstream.headers.get('content-type');
    if (upstreamType) responseHeaders.set('content-type', upstreamType);
    for (const header of ['x-marketmind-cache']) {
      const value = upstream.headers.get(header);
      if (value) responseHeaders.set(header, value);
    }
    const upstreamTiming = upstream.headers.get('server-timing');
    const proxyTiming = [
      `proxy_auth;dur=${authDuration.toFixed(1)}`,
      `proxy_backend;dur=${backendDuration.toFixed(1)}`,
      `proxy_total;dur=${(performance.now() - startedAt).toFixed(1)}`,
    ];
    responseHeaders.set('server-timing', [...(upstreamTiming ? [upstreamTiming] : []), ...proxyTiming].join(', '));
    responseHeaders.set('cache-control', 'no-store, private, max-age=0');
    return new NextResponse(upstream.body, {status: upstream.status, headers: responseHeaders});
  } catch {
    return NextResponse.json({detail: 'Service temporarily unavailable.'}, {status: 503, headers: {'Cache-Control': 'no-store'}});
  }
}

export {proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE};
