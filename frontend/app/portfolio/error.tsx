'use client';

import {RefreshCw, ShieldAlert} from 'lucide-react';

export default function PortfolioError({reset}:{error:Error & {digest?:string};reset:()=>void}) {
  return <section className="portfolio-route-error" role="alert"><ShieldAlert/><div><span className="eyebrow">PORTFOLIO DATA RECOVERY</span><h1>Portfolio data needs a refresh</h1><p>Your account and the rest of MarketMind are still available. Refresh this panel to request a clean portfolio snapshot.</p><button className="btn primary" onClick={reset}><RefreshCw/>Retry portfolio</button></div></section>;
}
