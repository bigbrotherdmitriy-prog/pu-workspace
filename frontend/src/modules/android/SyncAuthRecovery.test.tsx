import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SyncStatusPanel } from "./SyncStatusPanel";
afterEach(cleanup);
const summary={queued:0,syncing:0,attention:0,authRequired:1,conflicts:0,total:1};
it("offers a separate explicit session recovery instead of silently retrying a denial",()=>{
  const recover=vi.fn(),sync=vi.fn();render(<SyncStatusPanel online summary={summary} conflicts={[]} onSync={sync} onResolve={vi.fn()} onRecoverAuth={recover}/>);
  expect(recover).not.toHaveBeenCalled();fireEvent.click(screen.getByRole("button",{name:"Проверить сеанс и повторить"}));expect(recover).toHaveBeenCalledOnce();expect(sync).not.toHaveBeenCalled();
});
it("disables recovery offline and while the session check is running",()=>{
  const props={summary,conflicts:[],onSync:vi.fn(),onResolve:vi.fn(),onRecoverAuth:vi.fn()};
  const {rerender}=render(<SyncStatusPanel {...props} online={false}/>);expect(screen.getByRole("button",{name:"Проверить сеанс и повторить"})).toBeDisabled();
  rerender(<SyncStatusPanel {...props} online recoveringAuth/>);expect(screen.getByRole("button",{name:"Проверить сеанс и повторить"})).toBeDisabled();
});
