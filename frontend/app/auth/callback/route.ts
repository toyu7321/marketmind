import {NextRequest, NextResponse} from 'next/server';
import {createServerClient} from '@supabase/ssr';

export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const code = url.searchParams.get('code');
  const next = url.searchParams.get('next');
  const safeNext = next?.startsWith('/') && !next.startsWith('//') ? next : '/';
  const response = NextResponse.redirect(new URL(safeNext, url.origin));
  // Legacy Supabase invitation links can carry their authenticated session in
  // the URL fragment. A server route cannot read that fragment, so preserve it
  // through a redirect to the dedicated client-side consumer.
  if (!code) return NextResponse.redirect(new URL('/auth/accept-invite', url.origin));
  if (!process.env.NEXT_PUBLIC_SUPABASE_URL || !process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY) return NextResponse.redirect(new URL('/login?error=callback', url.origin));
  const supabase = createServerClient(process.env.NEXT_PUBLIC_SUPABASE_URL, process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY, {
    cookies: {
      getAll() { return request.cookies.getAll(); },
      setAll(values) { values.forEach(({name, value, options}) => response.cookies.set(name, value, options)); },
    },
  });
  const {error} = await supabase.auth.exchangeCodeForSession(code);
  return error ? NextResponse.redirect(new URL('/login?error=callback', url.origin)) : response;
}
