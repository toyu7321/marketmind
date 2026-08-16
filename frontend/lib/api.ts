const configuredBase = process.env.NEXT_PUBLIC_API_BASE_URL?.trim().replace(/\/+$/, '');

function resolveApiBase() {
  if (configuredBase) return configuredBase;
  if (typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname)) return 'http://localhost:8000/api';
  return '/api';
}

export function apiUrl(path: string) {
  const suffix = path.startsWith('/') ? path : `/${path}`;
  const apiBase = resolveApiBase();
  return `${apiBase.endsWith('/api') ? apiBase : `${apiBase}/api`}${suffix}`;
}

export async function api<T>(path:string,init?:RequestInit):Promise<T>{
  const res=await fetch(apiUrl(path),{...init,headers:{'Content-Type':'application/json',...(init?.headers||{})},cache:'no-store'});
  if(!res.ok)throw new Error('Market data temporarily unavailable');
  return res.json();
}
export const money=(v:number)=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(v);
