import type { OfflineCommand } from "./types";

const DB_NAME = "pu-mobile-sync-v1";
const DB_VERSION = 1;
const COMMANDS = "commands";
const META = "meta";

type MetaRow = { key: string; value: unknown };

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onerror = () => reject(request.error || new Error("Не удалось открыть локальную очередь"));
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(COMMANDS)) {
        const commands = db.createObjectStore(COMMANDS, { keyPath: "id" });
        commands.createIndex("userProject", ["userId", "projectId"], { unique: false });
        commands.createIndex("status", "status", { unique: false });
      }
      if (!db.objectStoreNames.contains(META)) db.createObjectStore(META, { keyPath: "key" });
    };
    request.onsuccess = () => resolve(request.result);
  });
}

function transact<T>(
  storeName: string,
  mode: IDBTransactionMode,
  action: (store: IDBObjectStore, resolve: (value: T) => void, reject: (reason?: unknown) => void) => void,
): Promise<T> {
  return openDatabase().then((db) => new Promise<T>((resolve, reject) => {
    const transaction = db.transaction(storeName, mode);
    const store = transaction.objectStore(storeName);
    let result: T;
    let resultReady = false;
    let failure: unknown;
    transaction.oncomplete = () => {
      db.close();
      if (resultReady) resolve(result);
      else reject(new Error("Локальная операция завершилась без результата"));
    };
    transaction.onabort = () => { db.close(); reject(failure || transaction.error); };
    transaction.onerror = () => { failure ||= transaction.error; };
    action(
      store,
      (value) => { result = value; resultReady = true; },
      (reason) => {
        failure = reason;
        try { transaction.abort(); } catch { reject(reason); }
      },
    );
  }));
}

export function putCommand(command: OfflineCommand): Promise<OfflineCommand> {
  return transact(COMMANDS, "readwrite", (store, resolve, reject) => {
    const request = store.put(command);
    request.onsuccess = () => resolve(command);
    request.onerror = () => reject(request.error);
  });
}

export function deleteCommand(id: string): Promise<void> {
  return transact(COMMANDS, "readwrite", (store, resolve, reject) => {
    const request = store.delete(id);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export function listCommands(userId: number, projectId: number): Promise<OfflineCommand[]> {
  return transact(COMMANDS, "readonly", (store, resolve, reject) => {
    const index = store.index("userProject");
    const request = index.getAll(IDBKeyRange.only([userId, projectId]));
    request.onsuccess = () => resolve((request.result as OfflineCommand[]).sort(
      (left, right) => left.clientCreatedAt.localeCompare(right.clientCreatedAt),
    ));
    request.onerror = () => reject(request.error);
  });
}

export function clearOfflineData(): Promise<void> {
  return openDatabase().then((db) => new Promise((resolve, reject) => {
    const transaction = db.transaction([COMMANDS, META], "readwrite");
    transaction.objectStore(COMMANDS).clear();
    transaction.objectStore(META).clear();
    transaction.oncomplete = () => { db.close(); resolve(); };
    transaction.onerror = () => { db.close(); reject(transaction.error); };
  }));
}

export function setMeta(key: string, value: unknown): Promise<void> {
  return transact(META, "readwrite", (store, resolve, reject) => {
    const request = store.put({ key, value } satisfies MetaRow);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export function getMeta<T>(key: string): Promise<T | undefined> {
  return transact(META, "readonly", (store, resolve, reject) => {
    const request = store.get(key);
    request.onsuccess = () => resolve((request.result as MetaRow | undefined)?.value as T | undefined);
    request.onerror = () => reject(request.error);
  });
}
