import {act,cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {CashFlowViews} from './CashFlowViews';
import {cashFixture} from './cashFlowViewsFixtures';
afterEach(cleanup);
function period(){fireEvent.change(screen.getByLabelText('ДДС с'),{target:{value:'2026-09-01'}});fireEvent.change(screen.getByLabelText('ДДС по'),{target:{value:'2026-09-01'}});}
const load=()=>fireEvent.click(screen.getByRole('button',{name:'Загрузить период ДДС'}));
it('explicit period fetch drives four views without mutations or money conversion',async()=>{
 const api=vi.fn().mockResolvedValue(cashFixture());render(<CashFlowViews projectId={1} contractId={null} api={api}/>);expect(api).not.toHaveBeenCalled();period();load();
 await screen.findByText(/Synthetic cash/);for(const label of ['Месяцы','Календарь','Сводка','Детали'])fireEvent.click(screen.getByRole('button',{name:label}));
 expect(screen.getByText('Поступление / План утверждён')).toBeInTheDocument();expect(screen.queryByText(/unconfirmed_plan/)).not.toBeInTheDocument();
 expect(api).toHaveBeenCalledTimes(1);expect(api.mock.calls[0][0]).toBe('/execution/cash-flow/views?project_id=1&date_from=2026-09-01&date_to=2026-09-01');expect(api.mock.calls[0][1]).not.toHaveProperty('method');
 expect(screen.getByText(/90071992547409.91/)).toBeInTheDocument();expect(screen.getByText(/Начальный банковский остаток неизвестен/)).toBeInTheDocument();
});
it('invalid period sends nothing; invalid sums hide details and raw diagnostics',async()=>{
 const raw=cashFixture();raw.summary.planned.net='0.00';const api=vi.fn().mockResolvedValue(raw);
 render(<CashFlowViews projectId={1} contractId={null} api={api}/>);load();expect(api).not.toHaveBeenCalled();period();load();
 await screen.findByRole('alert');expect(screen.queryByText(/Synthetic cash/)).not.toBeInTheDocument();
});
it('period ABA suppresses old reply and clears displayed snapshot while editing',async()=>{
 let resolve!:(v:unknown)=>void;const api=vi.fn().mockReturnValue(new Promise(r=>{resolve=r;}));
 render(<CashFlowViews projectId={1} contractId={null} api={api}/>);period();load();
 fireEvent.change(screen.getByLabelText('ДДС по'),{target:{value:'2026-09-02'}});fireEvent.change(screen.getByLabelText('ДДС по'),{target:{value:'2026-09-01'}});
 await act(async()=>resolve(cashFixture()));expect(screen.queryByText(/Synthetic cash/)).not.toBeInTheDocument();
});
it('project and contract switch refuse old replies and require another explicit period',async()=>{
 let resolve!:(v:unknown)=>void;const api=vi.fn().mockReturnValue(new Promise(r=>{resolve=r;}));
 const view=render(<CashFlowViews projectId={1} contractId={null} api={api}/>);period();load();
 view.rerender(<CashFlowViews projectId={2} contractId={3} api={api}/>);
 await act(async()=>resolve(cashFixture()));expect(screen.queryByText(/Synthetic cash/)).not.toBeInTheDocument();expect(screen.getByLabelText('ДДС с')).toHaveValue('');expect(api).toHaveBeenCalledTimes(1);
});
