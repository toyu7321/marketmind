import {redirect} from 'next/navigation';

import {AdminConsole} from '@/components/admin-console';
import {Shell} from '@/components/shell';
import {authServerConfigured, createSupabaseServerClient} from '@/lib/supabase/server';

export const dynamic = 'force-dynamic';

async function requireAdministrator() {
  if (!authServerConfigured() || !process.env.BACKEND_URL) redirect('/login?reason=configuration');
  const supabase = await createSupabaseServerClient();
  const {data: userData} = await supabase.auth.getUser();
  const {data: sessionData} = await supabase.auth.getSession();
  const token = sessionData.session?.access_token;
  if (!userData.user || !token) redirect('/login?reason=session');
  try {
    const response = await fetch(`${process.env.BACKEND_URL!.replace(/\/+$/, '')}/api/account`, {
      headers: {Authorization: `Bearer ${token}`},
      cache: 'no-store',
    });
    if (!response.ok) redirect('/');
    const account = await response.json() as {user?: {role?: string}; security?: {mfa_required_for_admin?: boolean; mfa_level?: string}};
    if (account.user?.role !== 'ADMIN') redirect('/');
    if (account.security?.mfa_required_for_admin && account.security.mfa_level !== 'aal2') redirect('/security?reason=mfa');
  } catch {
    redirect('/');
  }
}

export default async function AdminPage() {
  await requireAdministrator();
  return <Shell><AdminConsole/></Shell>;
}
