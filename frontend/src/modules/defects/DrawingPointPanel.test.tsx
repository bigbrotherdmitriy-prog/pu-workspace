import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { DrawingPointPanel, pointDraft } from "./DrawingPointPanel";
import { resumeOfflineSession } from "../../offline/lifecycle";
vi.mock("../../api/client", () => ({ api: vi.fn() }));
const request = vi.mocked(api); const done = vi.fn();
const props = { projectId: 4, userId: 1, defectId: 7, boundVersionId: null, point: null, canEdit: true, onBound: done };
const version = { id: 5, title: "SYNTHETIC volume", file_name: "SYNTHETIC.pdf", content_sha256: "a".repeat(64), created_at: null };
beforeEach(() => { resumeOfflineSession(1); vi.spyOn(window, "confirm").mockReturnValue(true); });
afterEach(() => { cleanup(); request.mockReset(); done.mockReset(); vi.restoreAllMocks(); resumeOfflineSession(); });
function mock() { request.mockImplementation(async (_path, init) => init?.method === "POST" ? { ...JSON.parse(String(init.body)), x: "0.25", y: "0.5", coordinate_system: "normalized_page_v1" } : { versions: [version], next_before_id: null, metadata_only: true }); }
async function fill() { await screen.findByText(/SYNTHETIC volume/); fireEvent.change(screen.getByLabelText("Версия чертежа"), { target: { value: "5" } }); fireEvent.change(screen.getByLabelText("Страница PDF"), { target: { value: "2" } }); fireEvent.change(screen.getByLabelText("Координата x"), { target: { value: "0,25" } }); fireEvent.change(screen.getByLabelText("Координата y"), { target: { value: "0.5" } }); }
it("validates coordinates without accepting nonfinite, exponent, outside page or fractional page", () => {
 expect(pointDraft(5,"2","0,25","1")).toEqual({ rd_version_id: 5, page: 2, x: .25, y: 1 });
 for (const x of ["NaN","Infinity","1.1","-0.1","1e-2","0.1234567", ""]) expect(() => pointDraft(5,"2",x,"0")).toThrow();
 for (const page of ["0","2.5","2147483648"]) expect(() => pointDraft(5,page,"0","1")).toThrow();
});
it("saves only selected scoped immutable metadata with expected actor and clear PDF limitation", async () => {
 mock(); render(<DrawingPointPanel {...props} />); await fill(); fireEvent.click(screen.getByText("Привязать точку"));
 await waitFor(() => expect(done).toHaveBeenCalledOnce()); const call=request.mock.calls.find(([,init])=>init?.method==="POST")!;
 expect(call[0]).toBe("/projects/4/defects/7/drawing-point"); expect(call[1]?.headers).toEqual({ "X-PU-Expected-User": "1" }); expect(JSON.parse(String(call[1]?.body))).toEqual({ rd_version_id:5,page:2,x:.25,y:.5 });
 expect(screen.getByText(/Число страниц автоматически не проверялось/)).toBeTruthy();
});
it("retains identical point after lost acknowledgement and disables editing before repeat", async () => {
 mock(); const original=request.getMockImplementation()!;let first=true;
 request.mockImplementation(async(path,init)=>{ if(init?.method==="POST"&&first){first=false;throw new Error("SYNTHETIC lost reply");} return original(path,init); });
 render(<DrawingPointPanel {...props} />); await fill(); fireEvent.click(screen.getByText("Привязать точку")); await screen.findByRole("alert");
 expect((screen.getByLabelText("Координата x") as HTMLInputElement).disabled).toBe(true);
 fireEvent.click(screen.getByText("Повторить исходную привязку")); await waitFor(()=>expect(done).toHaveBeenCalledOnce());
 const posts=request.mock.calls.filter(([,init])=>init?.method==="POST");expect(posts).toHaveLength(2);expect(posts[0][1]?.body).toBe(posts[1][1]?.body);expect(window.confirm).toHaveBeenCalledOnce();
});
it("does not request versions or offer writes for viewer or already fixed point", () => {
 const view=render(<DrawingPointPanel {...props} canEdit={false} />);expect(request).not.toHaveBeenCalled(); expect(screen.queryByText("Привязать точку")).toBeNull();
 view.rerender(<DrawingPointPanel {...props} point={{rd_version_id:5,page:2,x:"0.25",y:"0.5",coordinate_system:"normalized_page_v1"}} />);
 expect(request).not.toHaveBeenCalled();expect(screen.getByText(/на новую версию не переносится/)).toBeTruthy();
});
it("ignores late acknowledgement after project and actor change", async () => {
 mock();const original=request.getMockImplementation()!;let finish!:(value:unknown)=>void;
 request.mockImplementation(async(path,init)=>init?.method==="POST" ? new Promise(resolve=>{finish=resolve;}) : original(path,init));
 const view=render(<DrawingPointPanel {...props} />);await fill();fireEvent.click(screen.getByText("Привязать точку"));await waitFor(()=>expect(finish).toBeTypeOf("function"));
 resumeOfflineSession(2);view.rerender(<DrawingPointPanel {...props} projectId={8} userId={2} />);
 await act(async()=>finish({rd_version_id:5,page:2,x:"0.25",y:"0.5",coordinate_system:"normalized_page_v1"}));expect(done).not.toHaveBeenCalled();expect(screen.queryByText(/Привязка подтверждена/)).toBeNull();
});
it("rejects wrong acknowledgement and keeps original pending point", async()=>{
 mock(); const original=request.getMockImplementation()!;request.mockImplementation(async(path,init)=>init?.method==="POST" ? { rd_version_id:99,page:2,x:"0.25",y:"0.5",coordinate_system:"normalized_page_v1"} : original(path,init));
 render(<DrawingPointPanel {...props} />);await fill();fireEvent.click(screen.getByText("Привязать точку"));await screen.findByRole("alert");expect(done).not.toHaveBeenCalled();expect(screen.getByText("Повторить исходную привязку")).toBeTruthy();
});
it("loads older metadata by bounded cursor without inventing mutable file URL", async()=>{
 request.mockImplementation(async path=>path.includes("before_id=5") ? {versions:[{...version,id:3}],next_before_id:null,metadata_only:true} : {versions:[version],next_before_id:5,metadata_only:true});
 render(<DrawingPointPanel {...props} />);await screen.findByText(/SYNTHETIC volume/);fireEvent.click(screen.getByText("Загрузить предыдущие версии"));await waitFor(()=>expect(screen.getAllByRole("option")).toHaveLength(3));
 expect(request.mock.calls.some(([path])=>path==="/projects/4/rd/drawing-versions?limit=25&before_id=5")).toBe(true);
});
