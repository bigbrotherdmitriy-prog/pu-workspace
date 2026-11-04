/** Durable local fence and revocation-only queue. Never stores login tokens/passwords. */
import { closeOfflineSession, resumeOfflineSession } from '../offline/lifecycle';
const FENCE = 'pu.local-signout.v1';
const PREFIX = 'pu.logout-revoke.v1.';
const COOKIE = 'pu_local_signed_out';
let closed = false;
let generation = 0;
let activeTicket: string | null = null;
let flushing: Promise<void> | null = null;
const memoryQueue = new Set<string>();
const validTicket = (value: unknown): value is string => typeof value === 'string' && /^[1-9]\d{0,18}\.[a-f0-9]{64}$/.test(value);
export function signoutGeneration() { return generation; }
export function isLocallySignedOut() {
  try { return closed || localStorage.getItem(FENCE) !== null || document.cookie.split('; ').some(v => v === `${COOKIE}=1`); }
  catch { return true; } // Disabled storage must not silently restore a cookie session.
}
function queue(ticket: string) {
  if (!validTicket(ticket)) throw new Error('Не удалось подготовить отзыв сессии');
  memoryQueue.add(ticket);
  localStorage.setItem(PREFIX + ticket.split('.')[0], ticket);
}
function tickets() {
  const values = new Set(memoryQueue);
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key?.startsWith(PREFIX)) {
      const value = localStorage.getItem(key);
      if (!validTicket(value)) throw new Error('Повреждена очередь отзыва сессии');
      values.add(value);
    }
    if (values.size > 64) throw new Error('Очередь отзыва превышает безопасный лимит');
  }
  return values;
}
async function boundedFetch(path: string, options: RequestInit = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try { return await fetch(path, { ...options, cache: 'no-store', signal: controller.signal }); }
  finally { clearTimeout(timer); }
}
export async function prepareAuthenticatedSession(request?: () => Promise<{ logout_ticket?: unknown; user_id?: unknown }>) {
  const started = generation;
  if (isLocallySignedOut()) throw new Error('Требуется явный вход');
  const body = request ? await request() : await (async () => {
    const response = await boundedFetch('/auth/logout-intent', { credentials: 'same-origin' });
    if (!response.ok) throw new Error('Не удалось подготовить безопасный выход');
    return response.json();
  })();
  if (!validTicket(body.logout_ticket) || !Number.isSafeInteger(body.user_id) || body.user_id <= 0) throw new Error('Не подтверждена сессия');
  if (started !== generation || isLocallySignedOut()) throw new Error('Сеанс изменился');
  activeTicket = body.logout_ticket;
}
export function completeExplicitLogin(body: { logout_ticket?: unknown; user?: { id?: unknown } }, started: number) {
  if (!validTicket(body.logout_ticket) || !Number.isSafeInteger(body.user?.id) || Number(body.user?.id) <= 0) throw new Error('Вход не подтверждён сервером');
  if (started !== generation) {
    queue(body.logout_ticket); // A late login must not reopen a closed page.
    void flushPendingRevocations().catch(() => undefined);
    throw new Error('Вход отменён: сеанс изменился');
  }
  localStorage.removeItem(FENCE);
  document.cookie = `${COOKIE}=; Max-Age=0; Path=/; SameSite=Strict`;
  generation++; closed = false; activeTicket = body.logout_ticket;
  resumeOfflineSession(Number(body.user!.id));
}
export function beginLocalSignout(): string[] {
  closed = true; generation++; closeOfflineSession();
  document.cookie = `${COOKIE}=1; Max-Age=31536000; Path=/; SameSite=Strict`;
  document.cookie = 'pu_csrf=; Max-Age=0; Path=/; SameSite=Strict';
  const warnings: string[] = [];
  try { localStorage.setItem(FENCE, '1'); }
  catch { warnings.push('Не удалось сохранить локальный выход в хранилище'); }
  try { if (!activeTicket) throw new Error(); queue(activeTicket); }
  catch { warnings.push('Отзыв сессии не сохранён на диске; не закрывайте вкладку до появления сети'); }
  activeTicket = null;
  return warnings;
}
export function flushPendingRevocations(): Promise<void> {
  if (flushing) return flushing;
  flushing = (async () => {
    for (const ticket of tickets()) {
      // No ambient cookie: an old deferred response must not affect a new login.
      const response = await boundedFetch('/auth/logout/revoke', {
        method: 'POST', credentials: 'omit', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticket }),
      });
      if (!response.ok || (await response.json()).status !== 'revoked') throw new Error('Отзыв серверной сессии ожидает подтверждения');
      localStorage.removeItem(PREFIX + ticket.split('.')[0]); memoryQueue.delete(ticket);
    }
  })().finally(() => { flushing = null; });
  return flushing;
}
export async function clearOwnedBrowserCaches() {
  if (typeof caches !== 'undefined') {
    for (const name of await caches.keys()) if (name.startsWith('pu-workspace-')) await caches.delete(name);
  }
  // Pending commands, original photo Blobs, stock intents and unclassified drafts
  // are not disposable caches. Keep them scoped to their original actor.
  sessionStorage.removeItem('pu_active_project_id');
}
export function startLogoutRecovery(notify: (message: string) => void, signout: () => void) {
  let alive = true;
  const retry = () => { void flushPendingRevocations().then(() => {
    if (alive && isLocallySignedOut()) notify('Локальный выход выполнен. Серверный отзыв подтверждён. Для работы войдите явно.');
  }).catch(() => { if (alive && isLocallySignedOut()) notify('Локальный выход выполнен. Отзыв серверной сессии ожидает сети.'); }); };
  const storage = (event: StorageEvent) => {
    if (event.key === FENCE && event.newValue !== null) {
      // Each open page knows its own session, including a different account
      // that was active in another tab. Queue it before dropping page state.
      if (activeTicket) { try { queue(activeTicket); } catch { notify('Не удалось сохранить отзыв другой вкладки'); } }
      closed = true; generation++; activeTicket = null; closeOfflineSession(); signout(); retry();
    }
  };
  window.addEventListener('online', retry); window.addEventListener('storage', storage);
  const timer = setInterval(retry, 30000);
  retry();
  return () => { alive = false; clearInterval(timer); window.removeEventListener('online', retry); window.removeEventListener('storage', storage); };
}
