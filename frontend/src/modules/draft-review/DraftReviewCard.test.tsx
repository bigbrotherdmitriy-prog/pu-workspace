import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import { DraftReviewCard } from "./DraftReviewCard";
const first = { id: 9, subject: "Synthetic subject", body: "Synthetic text", recipient_to: "review@example.test", status: "draft", review_token: "a".repeat(64) };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function setup(canApprove = true) {
  const api = vi.fn().mockResolvedValue({ drafts: [first] });
  const onUpdated = vi.fn();
  const props = { projectId: 2, draftId: 9, title: "Synthetic preview", canEdit: true, canApprove, api, onUpdated };
  return { ...render(<DraftReviewCard {...props} />), api, onUpdated, props };
}
async function load() {
  fireEvent.click(screen.getByRole("button", { name: "Загрузить черновик для проверки" }));
  await screen.findByDisplayValue("Synthetic subject");
}
it("requires explicit exact loading, then approves only the reviewed token", async () => {
  const { api } = setup();
  expect(api).not.toHaveBeenCalled(); await load();
  api.mockResolvedValue({ ...first, status: "approved", review_token: "b".repeat(64) });
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить черновик" }));
  await screen.findByText("Точная версия подтверждена. Письмо не отправлено.");
  expect(JSON.parse(api.mock.calls[1][1].body)).toEqual({ status: "approved", expected_review_token: first.review_token });
  expect(api.mock.calls.some(([path]) => String(path).includes("send"))).toBe(false);
});
it("requires save and a new token before approving edited text", async () => {
  const { api } = setup(); await load();
  fireEvent.change(screen.getByLabelText("Текст ответа"), { target: { value: "Synthetic revised" } });
  expect(screen.getByRole("button", { name: "Подтвердить черновик" })).toBeDisabled();
  api.mockResolvedValue({ ...first, body: "Synthetic revised", review_token: "b".repeat(64) });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить правки черновика" }));
  await screen.findByText(/Правки сохранены\. Проверьте/);
  api.mockResolvedValue({ ...first, body: "Synthetic revised", status: "approved", review_token: "c".repeat(64) });
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить черновик" }));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(3));
  expect(JSON.parse(api.mock.calls[2][1].body)).toEqual({ status: "approved", expected_review_token: "b".repeat(64) });
});
it("preserves edits on 409 and does not silently retry or overwrite on cancelled reload", async () => {
  const { api } = setup(); await load();
  fireEvent.change(screen.getByLabelText("Текст ответа"), { target: { value: "Unsaved synthetic" } });
  api.mockRejectedValue(new ApiError("private raw", 409, "synthetic-request"));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить правки черновика" }));
  await screen.findByText(/Версия изменилась/);
  expect(screen.getByDisplayValue("Unsaved synthetic")).toBeInTheDocument();
  expect(screen.queryByText(/private raw/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить правки черновика" })).toBeDisabled();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", { name: "Перечитать черновик" }));
  expect(api).toHaveBeenCalledTimes(2);
});
it("keeps approval disabled for editors", async () => {
  setup(false); await load();
  expect(screen.getByRole("button", { name: "Подтвердить черновик" })).toBeDisabled();
});
it("refuses success with unreviewed content and preserves the local draft", async () => {
  const { api, onUpdated } = setup(); await load();
  onUpdated.mockClear();
  api.mockResolvedValue({ ...first, body: "Different unreviewed content", status: "approved", review_token: "b".repeat(64) });
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить черновик" }));
  await screen.findByText(/Версия изменилась/);
  expect(onUpdated).not.toHaveBeenCalled();
  expect(screen.getByDisplayValue(first.body)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Подтвердить черновик" })).toBeDisabled();
});
it("refuses a loaded draft without a review token", async () => {
  const { api } = setup(); api.mockResolvedValue({ drafts: [{ ...first, review_token: undefined }] });
  fireEvent.click(screen.getByRole("button", { name: "Загрузить черновик для проверки" }));
  await screen.findByText(/Актуальный черновик недоступен/);
  expect(screen.queryByRole("button", { name: "Подтвердить черновик" })).not.toBeInTheDocument();
});
it("rejects missing tokens and duplicate matching server rows", async () => {
  const { api } = setup(); api.mockResolvedValue({ drafts: [first, first] });
  fireEvent.click(screen.getByRole("button", { name: "Загрузить черновик для проверки" }));
  await screen.findByText(/Актуальный черновик недоступен/);
  expect(screen.queryByRole("button", { name: "Подтвердить черновик" })).not.toBeInTheDocument();
});
it("does not apply responses across project switches", async () => {
  const { api, rerender, props, onUpdated } = setup();
  let resolve!: (value: unknown) => void;
  api.mockImplementation(() => new Promise(r => { resolve = r; }));
  fireEvent.click(screen.getByRole("button", { name: "Загрузить черновик для проверки" }));
  rerender(<DraftReviewCard {...props} projectId={3} />);
  await act(async () => { resolve({ drafts: [first] }); });
  expect(onUpdated).not.toHaveBeenCalled();
  expect(screen.queryByDisplayValue("Synthetic subject")).not.toBeInTheDocument();
});
it("does not restore a pending response after project A to B to A", async () => {
  const { api, rerender, props, onUpdated } = setup();
  let resolve!: (value: unknown) => void;
  api.mockImplementation(() => new Promise(r => { resolve = r; }));
  fireEvent.click(screen.getByRole("button", { name: "Загрузить черновик для проверки" }));
  rerender(<DraftReviewCard {...props} projectId={3} />);
  rerender(<DraftReviewCard {...props} projectId={2} />);
  await act(async () => { resolve({ drafts: [first] }); });
  expect(onUpdated).not.toHaveBeenCalled();
  expect(screen.queryByDisplayValue("Synthetic subject")).not.toBeInTheDocument();
});
