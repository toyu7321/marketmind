'use client';

import Link from 'next/link';
import {usePathname, useRouter} from 'next/navigation';
import {useEffect, useMemo, useState} from 'react';
import {
  Activity, BarChart3, Bell, BriefcaseBusiness, ChartCandlestick, ChevronRight, Command, Compass,
  FileClock, Gauge, LogOut, Menu, Newspaper, PanelLeftClose, Search, Settings, ShieldCheck,
  SlidersHorizontal, Sparkles, UserRoundCog, X, Zap,
} from 'lucide-react';
import {api} from '@/lib/api';
import {authConfigured, createSupabaseBrowserClient} from '@/lib/supabase/client';

const nav = [
  ['/', 'Dashboard', Gauge], ['/markets', 'Markets', Compass], ['/scanner', 'Scanner', SlidersHorizontal],
  ['/stocks/NVDA', 'Stock Intel', ChartCandlestick], ['/news', 'News', Newspaper], ['/options', 'Options', BarChart3],
  ['/predictions', 'Predictions', FileClock], ['/backtest', 'Backtest', Activity], ['/portfolio', 'Portfolio', BriefcaseBusiness],
  ['/trading', 'Trading', Zap], ['/settings', 'Settings', Settings], ['/security', 'Security', ShieldCheck],
] as const;

type Account = {user:{display_name:string;email:string;role:string}};

export function Shell({children}: {children: React.ReactNode}) {
  const path = usePathname(); const router = useRouter();
  const [mobile, setMobile] = useState(false); const [palette, setPalette] = useState(false); const [kiosk, setKiosk] = useState(false);
  const [health, setHealth] = useState<Record<string, string>>(); const [account, setAccount] = useState<Account>();
  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setPalette(value => !value); } if (event.key === 'Escape') setPalette(false); };
    addEventListener('keydown', keyboard); api<Record<string, string>>('/health').then(setHealth).catch(() => undefined); api<Account>('/account').then(setAccount).catch(() => undefined);
    return () => removeEventListener('keydown', keyboard);
  }, []);
  const visibleNav = useMemo(() => account?.user.role === 'ADMIN' ? [...nav, ['/admin', 'Admin', UserRoundCog] as const] : nav, [account?.user.role]);
  const navigate = (target: string) => { router.push(target); setPalette(false); };
  const signOut = async () => { try { await api('/account/logout', {method:'POST'}); } catch {} if (authConfigured()) await createSupabaseBrowserClient().auth.signOut(); router.replace('/login'); };
  const appClass = 'app-shell' + (kiosk ? ' kiosk' : '');
  return <div className={appClass}><aside className={mobile ? 'open' : ''}>
    <div className="brand"><span className="brand-mark"><Sparkles size={17}/></span><div><b>MARKET<span>MIND</span></b><small>INTELLIGENCE TERMINAL</small></div><button className="mobile-close" onClick={() => setMobile(false)}><X/></button></div>
    <nav>{visibleNav.map(([href, label, Icon]) => <Link key={href} href={href} className={path === href || (href !== '/' && path.startsWith(href)) ? 'active' : ''} onClick={() => setMobile(false)}><Icon/><span>{label}</span><ChevronRight className="chev"/></Link>)}</nav>
    <div className="principle"><ShieldCheck/><span>DATA CALCULATES<br/><b>RISK CONTROLS</b></span></div>
  </aside>{mobile && <div className="scrim" onClick={() => setMobile(false)}/>}<div className="workspace">
    <header className="topbar"><button className="menu" onClick={() => setMobile(true)}><Menu/></button><div className="statuses"><span><i className="warn"/> MARKET STATUS</span><span><i className={health?.authentication === 'configured' ? '' : 'warn'}/> {health?.authentication === 'configured' ? 'AUTH SECURED' : 'AUTH CHECKING'}</span><span><i className="warn"/> AI RULES</span><span><i/> BROKER ISOLATED</span><span><i className="off"/> LIVE LOCKED</span></div><div className="top-actions"><button onClick={() => setPalette(true)}><Search/><span>Search</span><kbd>Ctrl K</kbd></button><button className="icon-btn"><Bell/></button><button className="icon-btn desktop" onClick={() => setKiosk(value => !value)} aria-label="Toggle kiosk mode"><PanelLeftClose/></button><button className="icon-btn" onClick={signOut} aria-label="Sign out"><LogOut/></button><span className="avatar">{(account?.user.display_name || account?.user.email || 'MM').slice(0, 2).toUpperCase()}</span></div></header>
    <main>{children}</main><nav className="mobile-nav">{visibleNav.slice(0, 5).map(([href, label, Icon]) => <Link key={href} href={href} className={path === href ? 'active' : ''}><Icon/><small>{label.split(' ')[0]}</small></Link>)}</nav>
  </div>{palette && <div className="palette-backdrop" onClick={() => setPalette(false)}><div className="palette" onClick={event => event.stopPropagation()}><div><Search/><input autoFocus placeholder="Search a symbol or press Enter" onKeyDown={event => { if (event.key === 'Enter' && event.currentTarget.value.trim()) navigate('/stocks/' + event.currentTarget.value.trim().toUpperCase()); }}/><kbd>ESC</kbd></div><span className="eyebrow">QUICK ACTIONS</span>{[['NVDA', 'Analyse NVIDIA', '/stocks/NVDA'], ['SC', 'Open Scanner', '/scanner'], ['BT', 'Run Backtest', '/backtest'], ['PF', 'Open Portfolio', '/portfolio'], ['OP', 'Open Options', '/options'], ['ST', 'Open Settings', '/settings'], ['SE', 'Security', '/security']].map(item => <button key={item[0]} onClick={() => navigate(item[2])}><i>{item[0]}</i><span>{item[1]}</span><Command/></button>)}</div></div>}</div>;
}
