import {Suspense} from 'react';

import {AcceptInvite} from '@/components/accept-invite';

export default function AcceptInvitePage() {
  return <Suspense fallback={<main className="auth-page" aria-busy="true"/>}><AcceptInvite/></Suspense>;
}
