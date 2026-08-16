'use client';

import Link from 'next/link';
import {usePathname, useRouter} from 'next/navigation';
import {useEffect, useState} from 'react';
import {
  Activity, BarChart3, Bell, BriefcaseBusiness, ChartCandlestick, ChevronRight, Command, Compass,
  FileClock, Gauge, Menu, Newspaper, PanelLeftClose, Search, Settings, ShieldCheck,
  SlidersHorizontal, Sparkles, X, Zap,
} from 'lucide-react';
import {api} from '@/lib/api';

const nav = [
  ['/', 'Dashboard', Gauge], ['/markets', 'Markets', Compass], ['/scanner', 'Scanner', SlidersHorizontal],
  ['/stocks/NVDA', 'Stock Intel', ChartCandlestick], ['/news', 'News', Newspaper], ['/options', 'Options', BarChart3],
  ['/predictions', 'Predictions', FileClock], ['/backtest', 'Backtest', Activity], ['/portfolio', 'Portfolio', BriefcaseBusiness],
  ['/trading', 'Trading', Zap], ['/settings', 'Settings', Settings],
] as const;

export function Shell({children}: {children: React.ReactNode}) {
  const path = usePathname();
  const router = useRouter();
  const [mobile, setMobile] = useState(false);
  const [palette, setPalette] = useState(false);
  const [kiosk, setKiosk] = useState(false);
  const [health, setHealth] = useState<Record<string, string>>();

  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPalette(value => !value);
      }
      if (event.key === 'Escape') setPalette(false);
    };
    addEventListener('keydown', keyboard);
    api<Record<string, string>>('/health').then(setHealth).catch(() => undefined);
    return () => removeEventListener('keydown', keyboard);
  }, []);

  const navigate = (target: string) => { router.push(target); setPalette(false); };
  const appClass = 'app-shell' + (kiosk ? ' kiosk' : '');
  return <div className={appClass}>
    <aside className={mobile ? 'open' : ''}>
      <div className="brand"><span className="brand-mark"><Sparkles size={17}/></span><div><b>MARKET<span>MIND</span></b><small>INTELLIGENCE TERMINAL</small></div><button className="mobile-close" onClick={() => setMobile(false)}><X/></button></div>
      <nav>{nav.map(([href, label, Icon]) => <Link key={href} href={href} className={path === href || (href !== '/' && path.startsWith(href)) ? 'active' : ''} onClick={() => setMobile(false)}><Icon/><span>{label}</span><ChevronRight className="chev"/></Link>)}</nav>
      <div className="principle"><ShieldCheck/><span>DATA CALCULATES<br/><b>RISK CONTROLS</b></span></div>
    </aside>
    {mobile && <div className="scrim" onClick={() => setMobile(false)}/>}
    <div className="workspace">
      <header className="topbar"><button className="menu" onClick={() => setMobile(true)}><Menu/></button><div className="statuses"><span><i className="warn"/> MARKET STATUS</span><span><i className={health?.market_provider === 'demo' ? 'warn' : ''}/> {health?.market_provider === 'demo' ? 'DEMO DATA' : health?.market_provider === 'alpaca configured' ? 'ALPACA READY' : 'DATA CHECKING'}</span><span><i className={health?.ai_provider === 'openai enabled' ? '' : 'warn'}/> {health?.ai_provider === 'openai enabled' ? 'AI ONLINE' : 'AI RULES'}</span><span><i/> BROKER PAPER</span><span><i className="off"/> LIVE OFF</span></div><div className="top-actions"><button onClick={() => setPalette(true)}><Search/><span>Search</span><kbd>Ctrl K</kbd></button><button className="icon-btn"><Bell/></button><button className="icon-btn desktop" onClick={() => setKiosk(value => !value)} aria-label="Toggle kiosk mode"><PanelLeftClose/></button><span className="avatar">MM</span></div></header>
      <main>{children}</main>
      <nav className="mobile-nav">{nav.slice(0, 5).map(([href, label, Icon]) => <Link key={href} href={href} className={path === href ? 'active' : ''}><Icon/><small>{label.split(' ')[0]}</small></Link>)}</nav>
    </div>
    {palette && <div className="palette-backdrop" onClick={() => setPalette(false)}><div className="palette" onClick={event => event.stopPropagation()}><div><Search/><input autoFocus placeholder="Search a symbol or press Enter" onKeyDown={event => { if (event.key === 'Enter' && event.currentTarget.value.trim()) navigate('/stocks/' + event.currentTarget.value.trim().toUpperCase()); }}/><kbd>ESC</kbd></div><span className="eyebrow">QUICK ACTIONS</span>{[['NVDA', 'Analyse NVIDIA', '/stocks/NVDA'], ['SC', 'Open Scanner', '/scanner'], ['BT', 'Run Backtest', '/backtest'], ['PF', 'Open Portfolio', '/portfolio'], ['OP', 'Open Options', '/options'], ['ST', 'Open Settings', '/settings']].map(item => <button key={item[0]} onClick={() => navigate(item[2])}><i>{item[0]}</i><span>{item[1]}</span><Command/></button>)}</div></div>}
  </div>;
}
