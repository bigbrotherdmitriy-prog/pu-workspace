import { fireEvent, render, screen, waitFor, act, cleanup } from '@testing-library/react';
import { expect, it, vi, afterEach } from 'vitest';
import { ScheduleGraphEditor } from './ScheduleGraphEditor';
import { planGraphFixture } from './schedulePlanFixtures';
const graph=()=>({...planGraphFixture(),status:'draft'});
afterEach(cleanup);
const save=()=>screen.getByRole('button',{name:'Сохранить состав и рассчитать'});
async function load(api:ReturnType<typeof vi.fn>){const view=render(<ScheduleGraphEditor projectId={4} baselineId={8} api={api} canEdit canApprove/>);await screen.findByLabelText('Название 12');return view;}
it('renames atomically, validates response, keeps approval separate',async()=>{
 const result=graph();result.items[0].title='Новое название';result.graph_revision=4;
 const api=vi.fn().mockResolvedValueOnce(graph()).mockResolvedValueOnce({...result,client_ref_map:{}});
 await load(api);fireEvent.change(screen.getByLabelText('Название 12'),{target:{value:'Новое название'}});
 expect(screen.getByRole('button',{name:'Сохранить и рассчитать'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'Проверить перед утверждением'})).toBeDisabled();
 fireEvent.click(save());await screen.findByText('Состав сохранён. Проверьте расчёт перед отдельным утверждением.');
 expect(api).toHaveBeenCalledTimes(2);expect(api.mock.calls[1][0]).toBe('/execution/baselines/8/graph/rows');
 const payload=JSON.parse(api.mock.calls[1][1].body);expect(payload.deleted_ids).toEqual([]);expect(payload.items).toHaveLength(3);expect(payload.items[0].title).toBe('Новое название');
 expect(screen.getByRole('button',{name:'Проверить перед утверждением'})).toBeEnabled();
});
it('new references and explicit deletion survive 409 without retry or automatic unlink',async()=>{
 const api=vi.fn().mockResolvedValueOnce(graph()).mockRejectedValueOnce({status:409});await load(api);
 fireEvent.click(screen.getByRole('button',{name:'Добавить этап'}));
 fireEvent.change(screen.getByLabelText('Связи new1'),{target:{value:'12FS+2d'}});
 fireEvent.click(screen.getByRole('button',{name:'Добавить этап'}));
 fireEvent.change(screen.getByLabelText('Связи new2'),{target:{value:'new1SS'}});
 fireEvent.click(screen.getByRole('button',{name:'Пометить удаление 14'}));fireEvent.click(save());
 await screen.findByText(/Сохранение не подтверждено/);
 const payload=JSON.parse(api.mock.calls[1][1].body);expect(payload.deleted_ids).toEqual([14]);
 expect(payload.items.at(-1).dependencies).toEqual([{predecessor_ref:'new1',link_type:'SS',lag_days:0}]);
 expect(screen.getByLabelText('Связи new2')).toHaveValue('new1SS');expect(save()).toBeDisabled();
 fireEvent.click(save());expect(api).toHaveBeenCalledTimes(2);
});
it('rejects dangling deletion locally; approved graph cannot add',async()=>{
 const api=vi.fn().mockResolvedValueOnce(graph());const view=await load(api);
 fireEvent.click(screen.getByRole('button',{name:'Пометить удаление 12'}));fireEvent.click(save());
 await screen.findByText(/удалённые этапы нельзя/);expect(api).toHaveBeenCalledTimes(1);view.unmount();
 render(<ScheduleGraphEditor projectId={4} baselineId={8} api={vi.fn().mockResolvedValue(planGraphFixture())} canEdit/>);
 expect(await screen.findByRole('button',{name:'Добавить этап'})).toBeDisabled();
});
it('discards late save after project change and blocks reload during write',async()=>{
 let resolve!:(value:unknown)=>void;const pending=new Promise(r=>{resolve=r;});
 const api=vi.fn().mockResolvedValueOnce(graph()).mockReturnValueOnce(pending).mockResolvedValueOnce({...graph(),baseline_id:9});
 const view=await load(api);fireEvent.change(screen.getByLabelText('Название 12'),{target:{value:'Старый проект'}});fireEvent.click(save());
 expect(screen.getByRole('button',{name:'Обновить серверную версию'})).toBeDisabled();
 view.rerender(<ScheduleGraphEditor projectId={5} baselineId={9} api={api} canEdit/>);
 await screen.findByLabelText('Название 12');
 await act(async()=>resolve({...graph(),graph_revision:4,client_ref_map:{}}));
 await waitFor(()=>expect(screen.getByLabelText('Название 12')).not.toHaveValue('Старый проект'));
});
