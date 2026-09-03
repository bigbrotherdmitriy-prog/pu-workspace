const {test} = require("node:test");
const assert = require("node:assert/strict");
const UX = require("./state.js");
const step = (s, event, value) => UX.reduce(s, event, value);
function ready() { return ["evidence","context","claim"].reduce((s,e)=>step(s,e),UX.fresh()); }
function queued() { const s=ready(); return step(s,"approve",UX.fingerprint(s)); }
function created() { return step(step(queued(),"start"),"complete"); }
test("three decisions independent; evidence also separate",()=>{
  let s=UX.fresh(); assert.equal(step(s,"approve",UX.fingerprint(s)).receipt,null);
  s=step(s,"evidence"); assert.equal(s.context,false);
  s=step(s,"context"); assert.equal(s.claim,false); assert.equal(s.approval,null);
  s=step(s,"claim"); assert.equal(s.receipt,null);
});
test("create then separately approve cancellation keeps create history",()=>{
  let s=created(); const receipt=s.receipt;
  s=step(s,"cancel"); assert.equal(step(s,"cancel_complete").cancelReceipt,null);
  s=step(s,"cancel_approve","T7/r1"); s=step(s,"cancel_complete");
  assert.equal(s.task,"T7 • cancelled"); assert.equal(s.receipt,receipt); assert.ok(s.cancelReceipt);
});
test("double approval / execution creates one result",()=>{
  let s=ready(), token=UX.fingerprint(s);
  s=step(s,"approve",token); const length=s.history.length;
  s=step(s,"approve",token); assert.equal(s.history.length,length);
  s=step(step(s,"start"),"complete"); const done=s.history.length;
  s=step(s,"complete"); assert.equal(s.history.length,done);
});
test("immutable displayed proposal token rejects changed version",()=>{
  let s=ready(), token=UX.fingerprint(s);
  s=step(s,"edit",{title:"Другое название",assignee:s.assignee});
  s=step(s,"approve",token); assert.equal(s.approval,null); assert.match(s.notice,/изменились/);
});
for(const source of ["stale","unavailable","revoked","unknown"]) test(source+" blocks dispatch",()=>{
  let s=step(queued(),"source",source); s=step(s,"start"); assert.notEqual(s.status,"running");
});
test("job completed is not successful effect",()=>{
  const s=step(step(queued(),"start"),"unknown");
  assert.equal(s.job,"completed"); assert.equal(s.receipt,null);
  assert.equal(step(s,"start").status,"unknown");
  assert.equal(step(s,"approve",UX.fingerprint(s)).status,"unknown");
});
test("known no-effect is separate from unknown",()=>{
  const s=step(step(queued(),"start"),"no_effect"); assert.equal(s.status,"failed"); assert.equal(s.receipt,null);
});
for(const event of ["revoke","expire"]) test(event+" blocks create and cancel",()=>{
  assert.notEqual(step(step(queued(),event),"start").status,"running");
  let s=step(step(created(),"cancel"),"cancel_approve","T7/r1");
  s=step(s,event); s=step(s,"cancel_complete"); assert.equal(s.cancelReceipt,null);
});
test("late reply cannot switch project or confirm current view",()=>{
  let s=step(step(UX.fresh(),"evidence"),"hold"); s=step(s,"project","old"); s=step(s,"release");
  assert.equal(s.project,"old"); assert.equal(s.context,false); assert.match(s.notice,/Поздний/);
});
test("409 cancels old authorization and forbids stale cancel target",()=>{
  let s=step(queued(),"conflict"); assert.equal(s.approval,null);
  s=step(step(created(),"cancel"),"cancel_approve","T7/r1"); s=step(s,"conflict");
  s=step(s,"cancel_approve","T7/r1"); s=step(s,"cancel_complete"); assert.equal(s.cancelReceipt,null);
});
test("state serialized during execution restores without new mutation",()=>{
  const s=step(queued(),"start"); assert.deepEqual(JSON.parse(JSON.stringify(s)),s);
});
test("source recovery never auto-reviews evidence or approves",()=>{
  let s=step(queued(),"source","stale"); s=step(s,"source","fresh");
  assert.equal(s.evidence,false); assert.equal(s.approval,null);
});
test("project round trip invalidates old request generation",()=>{
  let s=step(step(UX.fresh(),"evidence"),"hold");
  s=step(step(s,"project","old"),"project","4"); s=step(s,"release");
  assert.equal(s.context,false); assert.match(s.notice,/Поздний/);
});
test("late approval in another project cannot authorize or switch view",()=>{
  let s=ready(), token=UX.fingerprint(s);
  s=step(s,"project","old"); s=step(s,"approve",token);
  assert.equal(s.approval,null); assert.equal(s.project,"old");
});
