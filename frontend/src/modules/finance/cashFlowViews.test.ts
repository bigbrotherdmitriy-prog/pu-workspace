import {expect,it} from 'vitest';
import {parseCashFlowViews,cents,periodDays} from './cashFlowViewsModel';
import {cashFixture,cashScope} from './cashFlowViewsFixtures';
it('accepts exact one-snapshot decimal views',()=>expect(parseCashFlowViews(cashFixture(),cashScope).summary.planned.inflow).toBe('90071992547409.91'));
it.each(['amount','scope','calendar','id','proposed'])('rejects inconsistent %s',kind=>{
 const raw=cashFixture();
 if(kind==='amount')raw.summary.planned.inflow='90071992547409.90';
 if(kind==='scope')raw.scope.project_id=2;
 if(kind==='calendar')raw.calendar=[];
 if(kind==='id')raw.calendar[0].planned_entry_ids=[99];
 if(kind==='proposed')raw.details[0].status='proposed';
 expect(()=>parseCashFlowViews(raw,cashScope)).toThrow();
});
it.each([0.1,'1.001','NaN','1e2','-0.00'])('rejects noncanonical money %s',value=>expect(()=>cents(value)).toThrow());
it('rejects duplicate details, missing month, and cross-contract scope',()=>{
 const raw=cashFixture();raw.details.push({...raw.details[0]});expect(()=>parseCashFlowViews(raw,cashScope)).toThrow();
 raw.details.pop();raw.months=[];expect(()=>parseCashFlowViews(raw,cashScope)).toThrow();
 expect(()=>parseCashFlowViews(cashFixture(),{...cashScope,contract_id:9})).toThrow();
});
it('bounds dates to inclusive 366 and supports leap-day empty buckets',()=>{
 expect(periodDays({...cashScope,date_from:'2024-01-01',date_to:'2024-12-31'})).toHaveLength(366);
 expect(()=>periodDays({...cashScope,date_from:'2024-01-01',date_to:'2025-01-01'})).toThrow();
});
it('shows legacy unconfirmed plan as excluded rather than confirmed money',()=>{
 const raw=cashFixture();raw.details[0].review_status='pending_confirmation';raw.details[0].plan_in_period=false;raw.details[0].exclusion_reasons=['unconfirmed_plan'];raw.excluded.unconfirmed_plan=1;
 raw.calendar[0].planned_entry_ids=[];for(const bucket of [raw.summary,raw.calendar[0],raw.months[0]])bucket.planned={inflow:'0.00',outflow:'0.00',net:'0.00'};
 expect(parseCashFlowViews(raw,cashScope).excluded.unconfirmed_plan).toBe(1);
});
