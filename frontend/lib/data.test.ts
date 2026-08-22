import {describe, expect, it} from 'vitest';

import {cacheKeyForPath, dataPolicyForPath} from './data';

describe('client data cache policy', () => {
  it('uses short market-data TTLs and background refreshes', () => {
    expect(dataPolicyForPath('/options/NVDA')).toMatchObject({scope: 'market', dedupingInterval: 15_000, refreshInterval: 20_000});
    expect(dataPolicyForPath('/news')).toMatchObject({scope: 'session', dedupingInterval: 30_000, refreshInterval: 45_000});
    expect(dataPolicyForPath('/stocks/NVDA?range=3M')).toMatchObject({scope: 'session', dedupingInterval: 60_000, refreshInterval: 90_000});
  });

  it('never gives account-specific endpoint entries a public cache key', () => {
    const dashboard = dataPolicyForPath('/dashboard');
    const portfolio = dataPolicyForPath('/portfolio');
    expect(cacheKeyForPath('user-a', '/dashboard', dashboard)).toEqual(['session', 'user-a', '/dashboard']);
    expect(cacheKeyForPath('user-b', '/dashboard', dashboard)).toEqual(['session', 'user-b', '/dashboard']);
    expect(cacheKeyForPath(null, '/portfolio', portfolio)).toBeNull();
  });
});
