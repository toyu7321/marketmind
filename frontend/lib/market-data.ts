export type MarketBadgeTone='good'|'bad'|'warn'|'neutral';

export function marketQualityTone(quality:string|undefined):MarketBadgeTone {
  if(quality==='LIVE'||quality==='IEX')return 'good';
  if(quality==='DEMO'||quality==='DELAYED'||quality==='STALE')return 'warn';
  return 'bad';
}

export function marketProviderLabel(status:Record<string,unknown>|undefined):string {
  if(!status)return 'MARKET DATA UNAVAILABLE';
  const provider=typeof status.market_provider==='string'?status.market_provider:'Market data';
  const feed=typeof status.market_feed==='string'?status.market_feed.toUpperCase():'';
  const connection=typeof status.market_data_status==='string'?status.market_data_status:typeof status.connection==='string'?status.connection:'UNAVAILABLE';
  return `${provider} · ${feed} · ${connection}`.replace(' ·  · ',' · ');
}
