import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { WbsTreeEditor } from './WbsTreeEditor';

const item = (id: number, title: string, parent: number | null, order: number, level: number, summary = false) => ({
  id, title, wbs_parent_id: parent, wbs_order: order, wbs_level: level, is_summary: summary,
  duration_days: summary ? null : 2, is_milestone: summary ? null : false, predecessor_ids: null,
});
const graph = () => ({ baseline_id: 8, version: 2, status: 'draft', graph_revision: 3, project_start: '2026-09-01',
  items: [item(1, 'Design phase', null, 0, 0, true), item(10, 'Prepare drawings', 1, 0, 1), item(20, 'Independent work', null, 1, 0)],
  plan: { tasks: [{ task_id: 10 }, { task_id: 20 }], topological_order: [10, 20] },
});
afterEach(cleanup);

it('renders a nested tree and keeps summary distinct from work', () => {
  const { container } = render(<WbsTreeEditor graph={graph()} baselineId={8} save={vi.fn()} />);
  expect(screen.getByText('Раздел · 1')).toBeInTheDocument();
  expect(screen.getByText('Работа · 10')).toBeInTheDocument();
  expect(screen.queryByLabelText('Дней 1')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Дней 10')).toHaveValue(2);
  expect(container.querySelectorAll('.wbs-tree-list > li')).toHaveLength(3);
});

it('creates summary and leaf locally without saving automatically', () => {
  const save = vi.fn(); render(<WbsTreeEditor graph={graph()} baselineId={8} save={save} />);
  fireEvent.click(screen.getByRole('button', { name: 'Добавить раздел' }));
  fireEvent.click(screen.getByRole('button', { name: 'Добавить работу' }));
  expect(screen.getByLabelText('Название new1')).toHaveValue('Новый раздел');
  expect(screen.getByLabelText('Название new2')).toHaveValue('Новая работа');
  expect(save).not.toHaveBeenCalled();
});

it('moves a task under a summary and sends the exact WBS intent', async () => {
  const result = graph(); result.graph_revision = 4; result.items[2].wbs_parent_id = 1; result.items[2].wbs_order = 1; result.items[2].wbs_level = 1;
  const save = vi.fn().mockResolvedValue({ ...result, client_ref_map: {} }); const onSaved = vi.fn();
  render(<WbsTreeEditor graph={graph()} baselineId={8} save={save} onSaved={onSaved} />);
  fireEvent.change(screen.getByLabelText('Родитель 20'), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить структуру и пересчитать' }));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(save.mock.calls[0][0].items.find((entry: { id?: number }) => entry.id === 20)).toMatchObject({ wbs_parent_id: 1, wbs_order: 1 });
});

it('blocks a local parent cycle and never calls save', () => {
  const nested = graph(); nested.items.splice(1, 0, item(2, 'Nested phase', 1, 0, 1, true)); nested.items[2].wbs_order = 1;
  const save = vi.fn(); render(<WbsTreeEditor graph={nested} baselineId={8} save={save} />);
  fireEvent.change(screen.getByLabelText('Родитель 1'), { target: { value: '2' } });
  expect(screen.getByRole('alert')).toHaveTextContent('Перемещение отклонено');
  expect(save).not.toHaveBeenCalled();
});

it('fails closed on an untrusted graph response', () => {
  const raw = graph(); delete (raw.items[0] as Partial<typeof raw.items[0]>).wbs_level;
  render(<WbsTreeEditor graph={raw} baselineId={8} save={vi.fn()} />);
  expect(screen.getByRole('alert')).toHaveTextContent('неподтверждённый WBS-ответ');
  expect(screen.queryByRole('button', { name: 'Добавить работу' })).not.toBeInTheDocument();
});

it('blocks an ambiguous save result and does not retry', async () => {
  const save = vi.fn().mockRejectedValue(new Error('network'));
  render(<WbsTreeEditor graph={graph()} baselineId={8} save={save} />);
  fireEvent.change(screen.getByLabelText('Название 20'), { target: { value: 'Changed work' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить структуру и пересчитать' }));
  await screen.findByText(/Автоматический повтор заблокирован/);
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить структуру и пересчитать' }));
  expect(save).toHaveBeenCalledTimes(1);
});

it('keeps approved and permission-denied graphs read only', () => {
  const approved = graph(); approved.status = 'approved';
  render(<WbsTreeEditor graph={approved} baselineId={8} save={vi.fn()} disabled />);
  expect(screen.getByRole('button', { name: 'Добавить раздел' })).toBeDisabled();
  expect(screen.getByLabelText('Название 10')).toBeDisabled();
});
