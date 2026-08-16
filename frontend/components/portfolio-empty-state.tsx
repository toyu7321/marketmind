import React from 'react';
import {BriefcaseBusiness} from 'lucide-react';

export function PortfolioEmptyState({compact=false}:{compact?:boolean}) {
  return <div className={`portfolio-empty${compact ? ' compact' : ''}`}><BriefcaseBusiness/><div><b>No positions yet</b><span>Add a manual position or connect a paper broker to begin tracking this account.</span></div></div>;
}
