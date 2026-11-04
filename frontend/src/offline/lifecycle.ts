// Ephemeral page-session fence, separate from persisted queue ownership and user pause.
let epoch = 0;
let open = true;
let actor: number | undefined;
export class OfflineSessionChanged extends Error {
  constructor() { super("Сеанс изменился; отложенная операция отменена."); }
}
export function offlineSessionEpoch() { return epoch; }
export function offlineSessionIsCurrent(expected: number) { return open && epoch === expected; }
export function requireOfflineSession(expected: number) {
  if (!offlineSessionIsCurrent(expected)) throw new OfflineSessionChanged();
}
export function offlineActorIsCurrent(expected: number) { return actor === undefined || actor === expected; }
export function captureOfflineSession(expectedActor?: number) {
  requireOfflineSession(epoch);
  if (expectedActor !== undefined && !offlineActorIsCurrent(expectedActor)) throw new OfflineSessionChanged();
  return epoch;
}
export function closeOfflineSession() { epoch += 1; open = false; actor = undefined; }
/** Called only with the actor supplied by the authenticated application state. */
export function resumeOfflineSession(currentActor?: number) {
  if (actor !== currentActor) epoch += 1;
  actor = currentActor; open = true;
}
