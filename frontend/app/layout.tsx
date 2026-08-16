import type {Metadata,Viewport} from 'next';
import './globals.css';
import './refinements.css';
import {PwaClient} from '@/components/pwa-client';

export const metadata:Metadata={
  title:'MarketMind — AI Market Intelligence Terminal',
  description:'Professional quantitative US market workstation',
  applicationName:'MarketMind',
  manifest:'/manifest.webmanifest',
  icons:{icon:[{url:'/icons/marketmind.svg',type:'image/svg+xml'},{url:'/icons/favicon-32.png',sizes:'32x32',type:'image/png'},{url:'/icons/icon-192.png',sizes:'192x192',type:'image/png'}],apple:[{url:'/icons/apple-touch-icon.png',sizes:'180x180',type:'image/png'}]},
  appleWebApp:{capable:true,statusBarStyle:'black-translucent',title:'MarketMind'},
  formatDetection:{telephone:false},
};
export const viewport:Viewport={themeColor:'#071014',colorScheme:'dark',width:'device-width',initialScale:1,viewportFit:'cover'};
export default function Layout({children}:{children:React.ReactNode}){return <html lang="en"><body><PwaClient/>{children}</body></html>}
