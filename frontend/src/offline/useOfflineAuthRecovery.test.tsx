import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const state=vi.hoisted(()=>({total:1}));
vi.mock("./connectivity",()=>({isOnline:()=>true,CONNECTIVITY_EVENT:"synthetic-connectivity"}));
vi.mock("./syncEngine",()=>({CHANGE_EVENT:"synthetic-queue-changed",offlineSummary:vi.fn(async()=>({queued:state.total,syncing:0,attention:0,authRequired:state.total,conflicts:0,total:state.total})),serverConflicts:vi.fn(async()=>[]),synchronize:vi.fn(async(_user:number,_project:number,manual:boolean)=>{if(manual)state.total=0;}),resolveConflict:vi.fn(),recoverAuthRequired:vi.fn(async()=>1)}));
import { recoverAuthRequired, synchronize } from "./syncEngine";
import { useOfflineSync } from "./useOfflineSync";
beforeEach(()=>{state.total=1;vi.stubGlobal("indexedDB",{});vi.mocked(recoverAuthRequired).mockReset();vi.mocked(recoverAuthRequired).mockResolvedValue(1);vi.mocked(synchronize).mockClear();});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it("performs explicit account recovery before retry, never from the automatic timer path",async()=>{
  const applied=vi.fn();const {result}=renderHook(()=>useOfflineSync(1,17,applied));
  await waitFor(()=>expect(synchronize).toHaveBeenCalled());
  expect(recoverAuthRequired).not.toHaveBeenCalled();vi.mocked(synchronize).mockClear();
  await act(async()=>result.current.recoverAuth());
  expect(recoverAuthRequired).toHaveBeenCalledWith(1,17);
  expect(synchronize).toHaveBeenCalledWith(1,17,true);
  expect(applied).toHaveBeenCalledOnce();
});
it("propagates failed recovery without submitting commands",async()=>{
  const {result}=renderHook(()=>useOfflineSync(1,17,vi.fn()));await waitFor(()=>expect(synchronize).toHaveBeenCalled());vi.mocked(synchronize).mockClear();
  vi.mocked(recoverAuthRequired).mockRejectedValue(new Error("Synthetic identity mismatch"));
  await act(async()=>{await expect(result.current.recoverAuth()).rejects.toThrow("Synthetic identity mismatch");});
  expect(synchronize).not.toHaveBeenCalled();expect(result.current.recoveringAuth).toBe(false);
});
it("does not start two recoveries for a double click",async()=>{
  const {result}=renderHook(()=>useOfflineSync(1,17,vi.fn()));await waitFor(()=>expect(synchronize).toHaveBeenCalled());
  let release!:(value:number)=>void;vi.mocked(recoverAuthRequired).mockImplementation(()=>new Promise(resolve=>{release=resolve;}));
  await act(async()=>{const first=result.current.recoverAuth();const second=result.current.recoverAuth();expect(recoverAuthRequired).toHaveBeenCalledTimes(1);release(1);await Promise.all([first,second]);});
});
it("does not submit an old scope after switching accounts during recovery",async()=>{
  const {result,rerender}=renderHook(({user})=>useOfflineSync(user,17,vi.fn()),{initialProps:{user:1}});await waitFor(()=>expect(synchronize).toHaveBeenCalled());
  let release!:(value:number)=>void;vi.mocked(recoverAuthRequired).mockImplementation(()=>new Promise(resolve=>{release=resolve;}));
  let recovery!:Promise<void>;act(()=>{recovery=result.current.recoverAuth();});
  rerender({user:2});await waitFor(()=>expect(synchronize).toHaveBeenCalledWith(2,17,false));vi.mocked(synchronize).mockClear();
  await act(async()=>{release(1);await recovery;});
  expect(synchronize).not.toHaveBeenCalledWith(1,17,true);
});
