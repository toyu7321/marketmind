import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import {describe, expect, it, vi} from 'vitest';

type FetchEvent = {request:Record<string, unknown>; response?:Promise<unknown>; waits:Promise<unknown>[]; respondWith:(value:Promise<unknown>)=>void; waitUntil:(value:Promise<unknown>)=>void};

function fetchListener(options:{cacheMatch?:ReturnType<typeof vi.fn>;cacheOpen?:ReturnType<typeof vi.fn>;fetch?:ReturnType<typeof vi.fn>}={}) {
  const listeners = new Map<string, (event: FetchEvent)=>void>();
  const self = {location:{origin:'https://marketmind.test'}, clients:{claim:vi.fn()}, skipWaiting:vi.fn(), addEventListener:(name:string, listener:(event:FetchEvent)=>void) => listeners.set(name, listener)};
  const caches = {match:options.cacheMatch ?? vi.fn(() => Promise.resolve(undefined)), open:options.cacheOpen ?? vi.fn(() => Promise.resolve({put:vi.fn(() => Promise.resolve())})), keys:vi.fn(() => Promise.resolve([])), delete:vi.fn()};
  const source = readFileSync(fileURLToPath(new URL('../public/sw.js', import.meta.url)), 'utf8');
  vm.runInNewContext(source, {self, caches, fetch:options.fetch ?? vi.fn(), URL, Response:{error:vi.fn(() => ({kind:'error'}))}});
  return {listener:listeners.get('fetch')!, caches};
}

function eventFor(path:string): FetchEvent {
  const event: FetchEvent = {request:{method:'GET', url:`https://marketmind.test${path}`, mode:'cors'}, waits:[], respondWith(value){this.response=value;}, waitUntil(value){this.waits.push(value);}};
  return event;
}

describe('PWA static cache policy', () => {
  it('never intercepts or caches authenticated API responses', () => {
    const fetch = vi.fn(); const {listener, caches} = fetchListener({fetch}); const event = eventFor('/api/portfolio');
    listener(event);
    expect(event.response).toBeUndefined();
    expect(fetch).not.toHaveBeenCalled();
    expect(caches.match).not.toHaveBeenCalled();
  });

  it('clones a safe static response before asynchronous cache work can consume it', async () => {
    let resolveOpen: ((value:{put:ReturnType<typeof vi.fn>})=>void) | undefined;
    const put = vi.fn(() => Promise.resolve()); const open = vi.fn(() => new Promise<{put:ReturnType<typeof vi.fn>}>(resolve => { resolveOpen = resolve; }));
    const cacheCopy = {copy:true}; const response = {ok:true, clone:vi.fn(() => cacheCopy)}; const fetch = vi.fn(() => Promise.resolve(response));
    const {listener} = fetchListener({cacheOpen:open, fetch}); const event = eventFor('/icons/icon-192.png');
    listener(event);
    await event.response;
    expect(response.clone).toHaveBeenCalledTimes(1);
    expect(event.waits).toHaveLength(1);
    resolveOpen!({put});
    await event.waits[0];
    expect(put).toHaveBeenCalledWith(event.request, cacheCopy);
  });
});
