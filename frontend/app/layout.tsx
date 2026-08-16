import type {Metadata,Viewport} from 'next'; import './globals.css'; import './refinements.css';
export const metadata:Metadata={title:'MarketMind — AI Market Intelligence Terminal',description:'Professional quantitative US market workstation',manifest:'/manifest.webmanifest'};
export const viewport:Viewport={themeColor:'#071014',width:'device-width',initialScale:1,viewportFit:'cover'};
export default function Layout({children}:{children:React.ReactNode}){return <html lang="en"><body>{children}</body></html>}
