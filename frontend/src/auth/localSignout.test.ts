// Codex fallback: actual persisted coordinator/API fence, synthetic responses only.
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const ticket = '1.' + 'a'.repeat(64), nextTicket = '2.' + 'b'.repeat(64);
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: {'Content-Type':'application/json'} });
let auth: typeof import('./localSignout');
let network: ReturnType<typeof vi.fn>;
beforeEach(async () => {
  vi.resetModules(); localStorage.clear(); sessionStorage.clear();
  for (const key of ['pu_local_signed_out','pu_csrf']) document.cookie = `${key}=; Max-Age=0; Path=/`;
  network = vi.fn(async () => reply({logout_ticket:ticket,user_id:1})); vi.stubGlobal('fetch', network);
  auth = await import('./localSignout');
});
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); document.cookie='pu_local_signed_out=; Max-Age=0; Path=/'; });
async function prepared() { await auth.prepareAuthenticatedSession(); network.mockClear(); }

it('closes synchronously offline, clears readable auth cookie, persists revoke-only queue', async () => {
  await prepared(); document.cookie='pu_csrf=SYNTHETIC; Path=/';
  network.mockRejectedValue(new TypeError('SYNTHETIC offline'));
  expect(auth.beginLocalSignout()).toEqual([]);
  expect(auth.isLocallySignedOut()).toBe(true); expect(document.cookie).not.toContain('pu_csrf=');
  expect(localStorage.getItem('pu.logout-revoke.v1.1')).toBe(ticket);
  await expect(auth.flushPendingRevocations()).rejects.toThrow();
  expect(localStorage.getItem('pu.logout-revoke.v1.1')).toBe(ticket);
});
it('reload cannot auto-restore a still valid cookie; protected API never fetches', async () => {
  await prepared(); auth.beginLocalSignout(); vi.resetModules();
  auth=await import('./localSignout'); expect(auth.isLocallySignedOut()).toBe(true);
  network.mockClear(); const {api}=await import('../api/client');
  await expect(api('/auth/me')).rejects.toThrow(/закрыт/); expect(network).not.toHaveBeenCalled();
  await expect(auth.prepareAuthenticatedSession()).rejects.toThrow(/явный/);
});
it('reconnect revokes original capability without ambient cookie and retains local fence', async () => {
  await prepared(); auth.beginLocalSignout(); network.mockResolvedValue(reply({status:'revoked'}));
  await auth.flushPendingRevocations();
  const [path,options]=network.mock.calls[0]; expect(path).toBe('/auth/logout/revoke');
  expect((options as RequestInit).credentials).toBe('omit'); expect(JSON.parse(String((options as RequestInit).body))).toEqual({ticket});
  expect(localStorage.getItem('pu.logout-revoke.v1.1')).toBeNull(); expect(auth.isLocallySignedOut()).toBe(true);
});
it('only confirmed explicit login can remove the durable fence', async () => {
  await prepared(); auth.beginLocalSignout(); network.mockResolvedValue(reply({status:'revoked'})); await auth.flushPendingRevocations();
  const started=auth.signoutGeneration(); auth.completeExplicitLogin({logout_ticket:nextTicket,user:{id:2}},started);
  expect(auth.isLocallySignedOut()).toBe(false); expect(localStorage.getItem('pu.local-signout.v1')).toBeNull();
});
it('late login after logout is queued for revocation rather than reopening UI', async () => {
  await prepared(); const started=auth.signoutGeneration(); auth.beginLocalSignout(); network.mockRejectedValue(new Error('SYNTHETIC offline'));
  expect(()=>auth.completeExplicitLogin({logout_ticket:nextTicket,user:{id:2}},started)).toThrow(/отменён/);
  expect(auth.isLocallySignedOut()).toBe(true); expect(localStorage.getItem('pu.logout-revoke.v1.2')).toBe(nextTicket);
});
it('network online event retries but never silently signs in', async () => {
  await prepared(); auth.beginLocalSignout(); network.mockRejectedValue(new Error('SYNTHETIC offline'));
  const notice=vi.fn(),signout=vi.fn(),stop=auth.startLogoutRecovery(notice,signout);
  try { await vi.waitFor(()=>expect(notice).toHaveBeenCalled()); network.mockResolvedValue(reply({status:'revoked'})); window.dispatchEvent(new Event('online'));
    await vi.waitFor(()=>expect(localStorage.getItem('pu.logout-revoke.v1.1')).toBeNull()); expect(auth.isLocallySignedOut()).toBe(true);
  } finally { stop(); }
});
it('cross-tab signout closes page epoch and does not reopen on marker removal', async () => {
  const notice=vi.fn(),signout=vi.fn(),stop=auth.startLogoutRecovery(notice,signout);
  try { window.dispatchEvent(new StorageEvent('storage',{key:'pu.local-signout.v1',newValue:'1'})); expect(signout).toHaveBeenCalledOnce();
    window.dispatchEvent(new StorageEvent('storage',{key:'pu.local-signout.v1',newValue:null})); expect(auth.isLocallySignedOut()).toBe(true);
  } finally { stop(); }
});
it('owned cache purge preserves unrelated caches and all unsent data', async () => {
  const remove=vi.fn(async(_name: string)=>true);vi.stubGlobal('caches',{keys:async()=>['pu-workspace-shell-v1','OTHER-CACHE'],delete:remove});
  localStorage.setItem('pu-estimate-draft:1','SYNTHETIC unsent'); sessionStorage.setItem('pu_active_project_id','1');
  await auth.clearOwnedBrowserCaches();expect(remove.mock.calls.map(c=>c[0])).toEqual(['pu-workspace-shell-v1']);
  expect(localStorage.getItem('pu-estimate-draft:1')).toContain('unsent');expect(sessionStorage.getItem('pu_active_project_id')).toBeNull();
});
it('failed or malformed revoke ACK never drops queued capability', async () => {
  await prepared();auth.beginLocalSignout();network.mockResolvedValue(reply({status:'wrong'}));
  await expect(auth.flushPendingRevocations()).rejects.toThrow();expect(localStorage.getItem('pu.logout-revoke.v1.1')).toBe(ticket);
});
it('BFCache page checks durable fence on pageshow even without a storage event',async()=>{
  await prepared();network.mockRejectedValue(new Error('SYNTHETIC offline'));
  const notice=vi.fn(),close=vi.fn(),stop=auth.startLogoutRecovery(notice,close);
  try {localStorage.setItem('pu.local-signout.v1','1');window.dispatchEvent(new Event('pageshow'));
    expect(close).toHaveBeenCalledOnce();expect(auth.isLocallySignedOut()).toBe(true);
    expect(localStorage.getItem('pu.logout-revoke.v1.1')).toBe(ticket);
  } finally {stop();}
});
it('an in-flight old API response cannot publish data after local logout',async()=>{
  await prepared();let release!:(response:Response)=>void;
  network.mockImplementation(()=>new Promise<Response>(resolve=>{release=resolve;}));
  const {api}=await import('../api/client');const pending=api('/SYNTHETIC-private-data');
  auth.beginLocalSignout();release(reply({private:'SYNTHETIC old session'}));
  await expect(pending).rejects.toMatchObject({status:401});
});

it('an API body completing after logout cannot publish private data',async()=>{
  await prepared();let release!:(value:unknown)=>void,reading!:()=>void;
  const entered=new Promise<void>(resolve=>{reading=resolve;});
  const body=new Promise<unknown>(resolve=>{release=resolve;});
  network.mockResolvedValue({ok:true,status:200,headers:new Headers(),json:()=>{reading();return body;}});
  const {api}=await import('../api/client');const pending=api('/SYNTHETIC-private-body');
  await entered;auth.beginLocalSignout();release({private:'SYNTHETIC old body'});
  await expect(pending).rejects.toMatchObject({status:401});
});

// DeepSeek Login UI core
async function loginUiRender() {
  const React = await import('react');
  const rtl = await import('@testing-library/react/pure');
  const { Login } = await import('./Login');
  const onDone = vi.fn();
  const view = rtl.render(React.createElement(Login, { onDone }));
  return { rtl, onDone, view };
}
async function loginUiFill(rtl: typeof import('@testing-library/react/pure'), email: string, password: string) {
  rtl.fireEvent.change(rtl.screen.getByLabelText('Email'), { target: { value: email } });
  rtl.fireEvent.change(rtl.screen.getByLabelText('Пароль'), { target: { value: password } });
  rtl.fireEvent.click(rtl.screen.getByRole('button', { name: 'Войти' }));
}
function loginUiNoSyntheticPassword() {
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    expect(String(localStorage.getItem(key!))).not.toContain('SYNTHETIC-password');
  }
}

it('login UI revokes old ticket before login and clears fence once', async () => {
  await prepared(); auth.beginLocalSignout();
  const calls: string[] = [];
  network.mockImplementation(async (path: string, options: RequestInit) => {
    calls.push(path);
    if (path === '/auth/logout/revoke') {
      expect(options.credentials).toBe('omit');
      expect(JSON.parse(String(options.body))).toEqual({ ticket });
      return reply({ status: 'revoked' });
    }
    if (path === '/auth/login') return reply({ logout_ticket: nextTicket, user: { id: 2 } });
    throw new Error('SYNTHETIC unexpected ' + path);
  });
  const { rtl, onDone, view } = await loginUiRender();
  try {
    loginUiFill(rtl, 'user@example.test', 'SYNTHETIC-password');
    await rtl.waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(calls).toEqual(['/auth/logout/revoke', '/auth/login']);
    expect(auth.isLocallySignedOut()).toBe(false);
    expect(localStorage.getItem('pu.local-signout.v1')).toBeNull();
    loginUiNoSyntheticPassword();
  } finally { view.unmount(); rtl.cleanup(); }
});

it('login UI keeps queued ticket and fence when revoke fails', async () => {
  await prepared(); auth.beginLocalSignout();
  network.mockImplementation(async (path: string) => {
    if (path === '/auth/logout/revoke') return reply({ detail: 'SYNTHETIC unavailable' }, 503);
    throw new Error('SYNTHETIC unexpected ' + path);
  });
  const { rtl, onDone, view } = await loginUiRender();
  try {
    loginUiFill(rtl, 'user@example.test', 'SYNTHETIC-password');
    await rtl.waitFor(() => expect(rtl.screen.getByText(/ожидает подтверждения/)).toBeTruthy());
    expect(network.mock.calls.map((c) => c[0])).toEqual(['/auth/logout/revoke']);
    expect(localStorage.getItem('pu.logout-revoke.v1.1')).toBe(ticket);
    expect(auth.isLocallySignedOut()).toBe(true);
    expect(onDone).not.toHaveBeenCalled();
    loginUiNoSyntheticPassword();
  } finally { view.unmount(); rtl.cleanup(); }
});

it('login UI rejects login without valid ticket and retains fence', async () => {
  localStorage.setItem('pu.local-signout.v1', '1');
  network.mockImplementation(async (path: string) => {
    if (path === '/auth/login') return reply({ user: { id: 2 } });
    throw new Error('SYNTHETIC unexpected ' + path);
  });
  const { rtl, onDone, view } = await loginUiRender();
  try {
    loginUiFill(rtl, 'user@example.test', 'SYNTHETIC-password');
    await rtl.waitFor(() => expect(rtl.screen.getByText(/Вход не подтверждён сервером/)).toBeTruthy());
    expect(localStorage.getItem('pu.local-signout.v1')).toBe('1');
    expect(auth.isLocallySignedOut()).toBe(true);
    expect(onDone).not.toHaveBeenCalled();
    loginUiNoSyntheticPassword();
  } finally { view.unmount(); rtl.cleanup(); }
});
