import {NextRequest, NextResponse} from 'next/server';

export function GET(request: NextRequest) {
  return NextResponse.redirect(new URL('/icons/favicon-32.png', request.url), 307);
}
