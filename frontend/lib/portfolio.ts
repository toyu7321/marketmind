export type PortfolioPosition = {
  symbol: string;
  quantity: number;
  average_cost: number;
  price: number;
  market_value: number;
  unrealized_pl: number;
  daily_pl: number;
  weight: number;
  sector: string;
};

export type PortfolioAllocation = Pick<PortfolioPosition, 'symbol' | 'sector' | 'market_value' | 'weight'>;
export type PortfolioOrder = {symbol: string; side: string; quantity: number; status: string; price: number};
export type PortfolioData = {
  mode: 'USER'; source: string; equity: number; total_value: number; cash: number; buying_power: number; exposure: number; day_change: number;
  positions: PortfolioPosition[]; allocation: PortfolioAllocation[]; orders: PortfolioOrder[]; equity_curve: number[];
};

const emptyPortfolio: PortfolioData = {
  mode: 'USER', source: 'Manual portfolio', equity: 0, total_value: 0, cash: 0, buying_power: 0, exposure: 0, day_change: 0,
  positions: [], allocation: [], orders: [], equity_curve: [],
};

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function number(value: unknown, fallback = 0) {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function text(value: unknown, fallback = '') {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function percent(value: unknown) {
  return Math.min(100, Math.max(0, number(value)));
}

function positions(value: unknown): PortfolioPosition[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap(item => {
    const row = record(item); const symbol = text(row?.symbol).toUpperCase();
    if (!symbol) return [];
    return [{symbol, quantity:number(row?.quantity), average_cost:number(row?.average_cost), price:number(row?.price), market_value:number(row?.market_value), unrealized_pl:number(row?.unrealized_pl), daily_pl:number(row?.daily_pl), weight:percent(row?.weight), sector:text(row?.sector, 'Unknown')}];
  }).sort((left, right) => right.market_value - left.market_value);
}

function allocation(value: unknown, fallback: PortfolioPosition[]): PortfolioAllocation[] {
  if (!Array.isArray(value)) return fallback.map(({symbol, sector, market_value, weight}) => ({symbol, sector, market_value, weight}));
  return value.flatMap(item => {
    const row = record(item); const symbol = text(row?.symbol).toUpperCase();
    return symbol ? [{symbol, sector:text(row?.sector, 'Unknown'), market_value:number(row?.market_value), weight:percent(row?.weight)}] : [];
  }).sort((left, right) => right.market_value - left.market_value);
}

function orders(value: unknown): PortfolioOrder[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap(item => {
    const row = record(item); const symbol = text(row?.symbol).toUpperCase();
    return symbol ? [{symbol, side:text(row?.side, 'ORDER'), quantity:number(row?.quantity), status:text(row?.status, 'Pending'), price:number(row?.price)}] : [];
  });
}

export function normalizePortfolio(value: unknown): PortfolioData {
  const raw = record(value);
  if (!raw) return {...emptyPortfolio};
  const normalizedPositions = positions(raw.positions);
  const cash = number(raw.cash);
  const equity = number(raw.equity, cash);
  return {
    mode: 'USER', source:text(raw.source, emptyPortfolio.source), equity, total_value:number(raw.total_value, equity), cash,
    buying_power:number(raw.buying_power, cash), exposure:percent(raw.exposure), day_change:number(raw.day_change),
    positions:normalizedPositions, allocation:allocation(raw.allocation, normalizedPositions), orders:orders(raw.orders),
    equity_curve:Array.isArray(raw.equity_curve) ? raw.equity_curve.filter((item): item is number => typeof item === 'number' && Number.isFinite(item)) : [],
  };
}
