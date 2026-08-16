import {describe, expect, it} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {PortfolioEmptyState} from '../components/portfolio-empty-state';
import {normalizePortfolio} from './portfolio';

describe('portfolio response normalization', () => {
  it('provides a valid zero-holdings portfolio for a brand-new user', () => {
    const portfolio = normalizePortfolio({mode:'USER', source:'Manual portfolio', equity:0, cash:0, buying_power:0, exposure:0, positions:[], orders:[], equity_curve:[]});
    expect(portfolio.positions).toEqual([]);
    expect(portfolio.allocation).toEqual([]);
    expect(portfolio.equity).toBe(0);
  });

  it('supplies safe defaults for missing optional portfolio fields', () => {
    const portfolio = normalizePortfolio({cash:250});
    expect(portfolio.equity).toBe(250);
    expect(portfolio.buying_power).toBe(250);
    expect(portfolio.positions).toEqual([]);
    expect(portfolio.orders).toEqual([]);
  });

  it('drops stale position and order records that lack a symbol before rendering', () => {
    const portfolio = normalizePortfolio({positions:[undefined, {symbol:'', quantity:1}, {symbol:'NVDA', quantity:2, market_value:100}], orders:[undefined, {side:'BUY'}, {symbol:'NVDA', side:'BUY', quantity:2, price:50}]});
    expect(portfolio.positions).toHaveLength(1);
    expect(portfolio.positions[0].symbol).toBe('NVDA');
    expect(portfolio.orders).toHaveLength(1);
    expect(portfolio.orders[0].symbol).toBe('NVDA');
  });

  it('renders the zero-holdings Portfolio state without an exception', () => {
    expect(renderToStaticMarkup(createElement(PortfolioEmptyState))).toContain('No positions yet');
  });
});
