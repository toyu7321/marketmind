import {createServerClient} from '@supabase/ssr';
import {NextRequest, NextResponse} from 'next/server';

const publicPaths = new Set(['/login', '/auth/callback', '/auth/accept-invite', '/offline', '/manifest.webmanifest', '/sw.js']);

function configured() {
  return Boolean(process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY);
}

function secureHeaders(response: NextResponse, request: NextRequest, privateResponse: boolean) {
  const supabaseOrigin = process.env.NEXT_PUBLIC_SUPABASE_URL || '';
  const connectSource = supabaseOrigin ? ` ${supabaseOrigin}` : '';
  response.headers.set('Content-Security-Policy', `default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; form-action 'self'; img-src 'self' data: blob:; font-src 'self' https://fonts.gstatic.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; script-src 'self' 'unsafe-inline'; connect-src 'self'${connectSource}; worker-src 'self'; manifest-src 'self'`);
  response.headers.set('X-Content-Type-Options', 'nosniff');
  response.headers.set('X-Frame-Options', 'DENY');
  response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  response.headers.set('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=(), usb=()');
  if (request.nextUrl.protocol === 'https:' || request.headers.get('x-forwarded-proto') === 'https') response.headers.set('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');
  if (privateResponse) response.headers.set('Cache-Control', 'no-store, private, max-age=0');
  return response;
}

export async function middleware(request: NextRequest) {
  const {pathname} = request.nextUrl;
  // The same-origin route handler applies its own authentication and returns
  // JSON errors. Do not turn API failures into HTML login redirects.
  if (pathname.startsWith('/api/')) return secureHeaders(NextResponse.next({request}), request, pathname !== '/api/health');
  const isPublic = publicPaths.has(pathname) || pathname.startsWith('/icons/');
  let response = NextResponse.next({request});
  if (isPublic) return secureHeaders(response, request, false);
  if (!configured()) {
    const login = new URL('/login', request.url);
    login.searchParams.set('reason', 'configuration');
    return secureHeaders(NextResponse.redirect(login), request, true);
  }
  const supabase = createServerClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!, {
    cookies: {
      getAll() { return request.cookies.getAll(); },
      setAll(values) {
        values.forEach(({name, value}) => request.cookies.set(name, value));
        response = NextResponse.next({request});
        values.forEach(({name, value, options}) => response.cookies.set(name, value, options));
      },
    },
  });
  const {data, error} = await supabase.auth.getClaims();
  if (error || !data?.claims?.sub) {
    const login = new URL('/login', request.url);
    login.searchParams.set('next', pathname);
    return secureHeaders(NextResponse.redirect(login), request, true);
  }
  return secureHeaders(response, request, true);
}

export const config = {matcher: ['/((?!_next/static|_next/image|favicon.ico).*)']};
