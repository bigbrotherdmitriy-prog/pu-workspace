/* Optional QA using an ALREADY installed Playwright. No install/package edits. */
const {chromium} = require(process.env.PU_UX_PLAYWRIGHT);
const assert = require("node:assert/strict");
const {pathToFileURL} = require("node:url");
const path = require("node:path");
const fs = require("node:fs");
(async()=>{
  const browser = await chromium.launch({headless:true, args:["--disable-gpu"]});
  const page = await browser.newPage({viewport:{width:1440,height:1100}});
  const failures=[], external=[];
  page.on("pageerror",e=>failures.push(e.message));
  const root=pathToFileURL(__dirname+path.sep).href;
  await page.route("**/*",route=>{
    if(route.request().url().startsWith(root)) return route.continue();
    external.push(route.request().url()); return route.abort();
  });
  const url=pathToFileURL(path.join(__dirname,"index.html")).href;
  const click=id=>page.locator("#"+id).click();
  const event=name=>page.locator('[data-event="'+name+'"]').click();
  const txt=id=>page.locator("#"+id).innerText();
  const reset=()=>click("reset");
  const review=async()=>{await click("evidence");await click("context");await click("claim");};
  const approve=async()=>{await click("open-approval");await click("approve");};
  try {
    await page.goto(url);
    await page.locator("#evidence").waitFor();
    console.log("Initial render", await page.evaluate(()=>({
      height:document.documentElement.scrollHeight, width:document.documentElement.scrollWidth,
      styles:document.styleSheets.length, ready:!!window.PilotUX
    })), failures);
    await page.screenshot({path:path.join(__dirname,"desktop.png"),fullPage:true});
    assert.equal(await page.locator("#open-approval").isDisabled(),true);
    await review(); await approve();
    assert.match(await txt("result-heading"),/Ожидает выполнения/);
    await event("start"); await page.reload();
    assert.match(await txt("result-heading"),/Выполняется/);
    await event("complete");
    assert.match(await txt("receipts"),/R25/);
    await click("cancel"); await click("approve"); await event("cancel_complete");
    assert.match(await txt("receipts"),/R25/); assert.match(await txt("receipts"),/R26/);
    await reset(); await click("evidence"); await event("hold");
    await page.locator("#project").selectOption("old"); await event("release");
    assert.equal(await page.locator("#project").inputValue(),"old");
    assert.equal(await page.locator("#flow").isVisible(),false);
    await page.reload(); assert.equal(await page.locator("#project").inputValue(),"old");
    await click("return"); assert.match(await txt("context-state"),/AI-гипотеза/);
    for(const source of ["stale","unavailable","revoked","unknown"]) {
      await page.locator("#source").selectOption(source);
      assert.equal(await page.locator("blockquote").count(),0);
      assert.equal(await page.locator("#open-approval").isDisabled(),true);
      assert.equal(await page.locator("[title]").count(),0);
    }
    await reset(); await review(); await approve(); await event("start"); await event("unknown");
    assert.match(await txt("result-heading"),/неизвестен/); assert.equal(await txt("receipts"),"");
    assert.equal(await page.locator("#open-approval").isDisabled(),true);
    await reset(); await review(); await approve(); await event("revoke");
    assert.equal(await page.locator('[data-event="start"]').isDisabled(),true);
    await approve(); await event("expire");
    assert.equal(await page.locator('[data-event="start"]').isDisabled(),true);
    await event("conflict"); assert.match(await txt("notice"),/409/);
    await click("open-approval");
    // Native dialog Escape and focus return, no hidden confirm on Enter.
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#approval").isVisible(),false);
    assert.equal(await page.locator("#open-approval").evaluate(el=>el===document.activeElement),true);
    await click("open-approval");
    assert.equal(await page.locator("#dismiss").evaluate(el=>el===document.activeElement),true);
    await page.keyboard.press("Tab");
    assert.equal(await page.locator("#approve").evaluate(el=>el===document.activeElement),true);
    await page.keyboard.press("Escape");
    await reset(); await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>scrollTo(0,0));
    await page.screenshot({path:path.join(__dirname,"mobile.png")});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await review(); await click("open-approval");
    assert.equal(await page.locator("#approval").evaluate(el=>el.scrollWidth<=el.clientWidth),true);
    await page.screenshot({path:path.join(__dirname,"approval-mobile.png")});
    assert.deepEqual(failures,[]); assert.deepEqual(external,[]);
    const result={browser:"Chromium",desktop:"1440x1100",mobile:"390x844",
      checks:["separate decisions","create/receipt/cancel","reload running","late context reply",
      "reload selected project","four evidence denials","unknown != completed",
      "revoke/expiry/409","Escape/focus/Tab","mobile overflow"],
      pageErrors:failures,externalRequests:external};
    fs.writeFileSync(path.join(__dirname,"browser-result.json"),JSON.stringify(result,null,2)+"\n");
    console.log(JSON.stringify(result));
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
