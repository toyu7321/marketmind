import {Suspense} from 'react';

import {AuthLogin} from '@/components/auth-login';

export default function LoginPage() {
  return <Suspense fallback={<main className="auth-page" aria-busy="true"/>}><AuthLogin/></Suspense>;
}
