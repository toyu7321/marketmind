import Link from 'next/link';

export default function OfflinePage() {
  return <main className="offline-page"><div className="offline-card"><div className="offline-mark">MM</div><span className="eyebrow">CACHED APPLICATION SHELL</span><h1>MARKETMIND OFFLINE</h1><p>Unable to reach market services. Live prices and trading actions are unavailable until your connection returns.</p><Link className="btn primary" href="/">Try reconnecting</Link></div></main>;
}
