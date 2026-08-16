'use client';

import {useEffect, useState} from 'react';
import {api} from '@/lib/api';

type WaitingWorker = ServiceWorker | null;

function isStandalone() {
  return window.matchMedia('(display-mode: standalone)').matches || Boolean((navigator as Navigator & {standalone?: boolean}).standalone);
}

export function PwaClient() {
  const [online, setOnline] = useState(true);
  const [starting, setStarting] = useState(false);
  const [updateWorker, setUpdateWorker] = useState<WaitingWorker>(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    const updateConnection = () => setOnline(navigator.onLine);
    updateConnection();
    addEventListener('online', updateConnection);
    addEventListener('offline', updateConnection);

    if (isStandalone()) {
      setStarting(true);
      const controller = new AbortController();
      api<Record<string, string>>('/health', {signal: controller.signal}).catch(() => undefined).finally(() => setStarting(false));
      const timeout = window.setTimeout(() => controller.abort(), 4500);
      return () => {
        removeEventListener('online', updateConnection);
        removeEventListener('offline', updateConnection);
        window.clearTimeout(timeout);
        controller.abort();
      };
    }

    return () => {
      removeEventListener('online', updateConnection);
      removeEventListener('offline', updateConnection);
    };
  }, []);

  useEffect(() => {
    if (!('serviceWorker' in navigator)) return;
    let alive = true;
    const watchRegistration = (registration: ServiceWorkerRegistration) => {
      if (registration.waiting && navigator.serviceWorker.controller) setUpdateWorker(registration.waiting);
      registration.addEventListener('updatefound', () => {
        const installing = registration.installing;
        installing?.addEventListener('statechange', () => {
          if (alive && installing.state === 'installed' && navigator.serviceWorker.controller) setUpdateWorker(registration.waiting);
        });
      });
    };
    navigator.serviceWorker.register('/sw.js', {scope: '/'}).then(watchRegistration).catch(() => undefined);
    const reloadOnControllerChange = () => { if (refreshing) window.location.reload(); };
    navigator.serviceWorker.addEventListener('controllerchange', reloadOnControllerChange);
    return () => {
      alive = false;
      navigator.serviceWorker.removeEventListener('controllerchange', reloadOnControllerChange);
    };
  }, [refreshing]);

  const refresh = () => {
    if (!updateWorker) return;
    setRefreshing(true);
    updateWorker.postMessage({type: 'SKIP_WAITING'});
    window.setTimeout(() => window.location.reload(), 1200);
  };

  return <>
    {starting && <div className="pwa-startup" role="status" aria-live="polite"><div className="pwa-startup-mark">MM</div><strong>MARKET<span>MIND</span></strong><small>AI MARKET INTELLIGENCE</small><i/><p>Connecting to market services…</p></div>}
    {!online && <div className="pwa-notice offline" role="status"><b>MARKETMIND OFFLINE</b><span>Cached app shell only. Live market services are unavailable.</span></div>}
    {updateWorker && <div className="pwa-notice update" role="status"><span>A new MarketMind version is available.</span><button onClick={refresh} disabled={refreshing}>{refreshing ? 'Refreshing…' : 'Refresh'}</button></div>}
  </>;
}
