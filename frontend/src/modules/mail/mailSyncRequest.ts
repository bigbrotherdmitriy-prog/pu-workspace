import type { MailFolderKind } from "./types";

/** A bounded explicit folder refresh, not a full mailbox/history import. */
export function mailSyncRequest(folder?: MailFolderKind) {
  const queries: Partial<Record<MailFolderKind, string>> = {
    inbox: "in:inbox", sent: "in:sent", spam: "in:spam", trash: "in:trash",
    archive: "-in:inbox -in:sent -in:drafts -in:spam -in:trash", all: "in:anywhere -in:drafts",
  };
  return { query: (folder && queries[folder]) || "newer_than:7d", max_results: 25 };
}
