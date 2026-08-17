import {describe,expect,it} from 'vitest';
import {marketProviderLabel,marketQualityTone} from './market-data';

describe('market-data display contract',()=>{
  it('distinguishes live/IEX data from demo and unavailable data',()=>{
    expect(marketQualityTone('IEX')).toBe('good');
    expect(marketQualityTone('LIVE')).toBe('good');
    expect(marketQualityTone('DEMO')).toBe('warn');
    expect(marketQualityTone('STALE')).toBe('warn');
    expect(marketQualityTone('UNAVAILABLE')).toBe('bad');
  });

  it('shows provider/feed/status without exposing configuration',()=>{
    expect(marketProviderLabel({market_provider:'Alpaca',market_feed:'iex',market_data_status:'Healthy'})).toBe('Alpaca · IEX · Healthy');
    expect(marketProviderLabel(undefined)).toBe('MARKET DATA UNAVAILABLE');
  });
});
