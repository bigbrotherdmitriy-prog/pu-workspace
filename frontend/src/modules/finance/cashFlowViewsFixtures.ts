export const cashScope={project_id:1,contract_id:null,date_from:'2026-09-01',date_to:'2026-09-01'};
export function cashFixture(){
 const planned={inflow:'90071992547409.91',outflow:'0.00',net:'90071992547409.91'};
 const actual={inflow:'0.00',outflow:'0.00',net:'0.00'};
 return {scope:{...cashScope},currency:'RUB',details:[{id:7,project_id:1,contract_id:null,record_version:1,title:'Synthetic cash',counterparty:null,direction:'inflow',status:'approved',review_status:'confirmed',planned_date:'2026-09-01',actual_date:null,planned_amount:planned.inflow,actual_amount:'0.00',plan_in_period:true,actual_in_period:false,exclusion_reasons:[] as string[]}],
 months:[{month:'2026-09',planned:{...planned},actual:{...actual}}],calendar:[{date:'2026-09-01',planned:{...planned},actual:{...actual},planned_entry_ids:[7],actual_entry_ids:[] as number[]}],summary:{planned:{...planned},actual:{...actual}},excluded:{proposed:0,cancelled:0,unsupported_status:0,invalid_actual:0,invalid_plan:0,invalid_direction:0,unconfirmed_plan:0},decision_requirements:[],external_effects:{payment_created:false,posting_created:false,automatic_conversion:false}};
}
