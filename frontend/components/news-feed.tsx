'use client';

import {useEffect, useMemo, useState} from 'react';

import {useApiData} from '@/lib/data';
import {marketQualityTone} from '@/lib/market-data';
import {Badge, ErrorState, Panel, Skeleton} from './ui';

type Article = Record<string, any>;
type NewsData = {mode:string;items?:Article[];message?:string;provider_status?:Record<string, string>};
const PAGE_SIZE = 20;

export function NewsFeed() {
  const {data, error, retry, isValidating} = useApiData<NewsData>('/news');
  const [tab, setTab] = useState('All');
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const items = useMemo(() => (data?.items || []).filter(item => tab === 'All' || item.category === tab || (tab === 'High Impact' && item.importance === 'High')), [data?.items, tab]);
  const visibleItems = items.slice(0, visibleCount);
  const counts = useMemo(() => ({
    positive: items.filter(item => item.sentiment === 'Positive').length,
    neutral: items.filter(item => item.sentiment === 'Neutral').length,
    negative: items.filter(item => item.sentiment === 'Negative').length,
  }), [items]);

  useEffect(() => { setVisibleCount(PAGE_SIZE); }, [tab]);

  if (error) return <ErrorState retry={retry}/>;
  if (!data) return <Skeleton/>;

  return <><div className="page-heading"><div><span className="eyebrow">INTELLIGENCE FEED / NORMALIZED + DEDUPLICATED</span><h1>Market News</h1><p>Ranked by recency, relevance, sentiment, and potential market impact.</p></div><div className="heading-actions"><Badge tone={marketQualityTone(data.mode)}>{isValidating ? 'REFRESHING' : data.mode} · {data.provider_status?.news_provider || 'News'}</Badge></div></div>
    <div className="data-status"><b>NEWS PROVIDER</b><span>{data.provider_status?.news_provider || 'Unavailable'} · {data.provider_status?.news_status || data.mode}</span><small>{data.provider_status?.last_successful_request ? `Last update ${new Date(data.provider_status.last_successful_request).toLocaleString()}` : data.message || 'No successful request yet'}</small></div>
    <div className="filter-pills page-tabs">{['All', 'Market', 'Watchlist', 'High Impact'].map(value => <button onClick={() => setTab(value)} className={tab === value ? 'active' : ''} key={value}>{value}</button>)}</div>
    <div className="news-layout"><Panel title="Intelligence Stream" eyebrow={`${items.length} PRIORITIZED ITEMS · ${data.mode}`}>{items.length ? <div className="news-feed">{visibleItems.map(item => <article key={item.id || item.headline}><time>{item.time}<small>ET</small></time><div><div><Badge tone={item.sentiment === 'Positive' ? 'good' : item.sentiment === 'Negative' ? 'bad' : 'neutral'}>{item.sentiment}</Badge><Badge tone={item.importance === 'High' ? 'warn' : 'neutral'}>{item.importance} impact</Badge><Badge tone={marketQualityTone(item.freshness)}>{item.provider || item.freshness}</Badge></div><h3>{item.headline}</h3><span>Affected · <b>{item.affected}</b></span><p>{item.why}</p></div></article>)}{visibleCount < items.length && <button className="btn news-load-more" onClick={() => setVisibleCount(count => count + PAGE_SIZE)}>Load {Math.min(PAGE_SIZE, items.length - visibleCount)} more articles</button>}</div> : <div className="source-empty"><b>News unavailable</b><span>{data.message || 'The configured provider returned no news. MarketMind has not generated replacement headlines.'}</span></div>}</Panel>
      <aside><Panel title="Signal Mix" eyebrow="CURRENT FEED">{items.length ? <><div className="donut"><strong>{Math.round(counts.positive / items.length * 100)}%</strong><span>Constructive</span></div><div className="sentiment-legend"><span><i className="up"/> Positive <b>{counts.positive}</b></span><span><i/> Neutral <b>{counts.neutral}</b></span><span><i className="down"/> Negative <b>{counts.negative}</b></span></div></> : <p className="panel-note">No provider articles to classify.</p>}</Panel><Panel title="Feed Status" eyebrow="SOURCE TRANSPARENCY"><p className="panel-note">Provider: {data.provider_status?.news_provider || 'Unavailable'}<br/>Connection: {data.provider_status?.news_status || data.mode}<br/>No browser or user data is sent to Alpaca.</p></Panel></aside></div>
  </>;
}
