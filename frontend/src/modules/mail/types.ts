export type MailFolderKind = "inbox" | "attention" | "drafts" | "sent" | "archive" | "spam" | "trash" | "all";

export type MailDeliveryStatus = "draft" | "approved" | "rejected" | "queued" | "sending" | "sent" | "failed" | "unknown";

export type MailAddress = {
  email: string;
  name?: string | null;
};

export type MailAttachment = {
  id?: string;
  attachment_id?: string;
  name: string;
  mime_type?: string;
  size?: number;
};

export type MailDraft = {
  id: number;
  project_id: number;
  contract_id?: number | null;
  message_id?: number | null;
  mode: "compose" | "reply" | "reply_all" | "forward";
  to: MailAddress[];
  cc: MailAddress[];
  bcc: MailAddress[];
  subject: string;
  body: string;
  body_format?: "plain" | "html";
  attachments: MailAttachment[];
  revision: number;
  approved_revision?: number | null;
  status: MailDeliveryStatus;
  safe_error?: string | null;
  receipt?: {
    provider?: string;
    provider_message_id?: string | null;
    sent_at?: string | null;
  } | null;
  created_at?: string;
  updated_at?: string;
};

export type MailMessage = {
  id: number;
  project_id: number;
  contract_id?: number | null;
  thread_id: string;
  direction: "incoming" | "outgoing";
  sender: MailAddress;
  to: MailAddress[];
  cc: MailAddress[];
  subject: string;
  preview?: string;
  content: string;
  summary?: string;
  received_at: string;
  status: string;
  needs_attention?: boolean;
  context_confirmed?: boolean;
  context_confidence?: number;
  context_evidence?: string;
  source_url?: string;
  attachments: MailAttachment[];
  drafts: MailDraft[];
};

export type MailThread = {
  id: string;
  subject: string;
  participants: MailAddress[];
  last_message_at: string;
  unread_count: number;
  needs_attention: boolean;
  messages: MailMessage[];
};

export type MailFolder = {
  kind: MailFolderKind;
  label: string;
  count?: number;
};

export type MailCapabilities = {
  provider: string;
  connected: boolean;
  can_send: boolean;
  can_compose: boolean;
  can_reply: boolean;
  can_reply_all: boolean;
  can_forward: boolean;
  can_attach: boolean;
  can_move: boolean;
  supports_threads: boolean;
  versioned_approval: boolean;
};

export type MailProject = { id: number; name: string };
export type MailContract = { id: number; number: string; title: string; project_id: number };

export type DraftInput = {
  project_id: number;
  contract_id: number | null;
  mode: MailDraft["mode"];
  reply_to_message_id: number | null;
  to: string[];
  cc: string[];
  bcc: string[];
  subject: string;
  body: string;
  body_format: "plain" | "html";
  attachments: Array<{ message_id: number; attachment_index: number }>;
};

export type MailSettings = {
  display_name: string;
  signature_html: string;
  auto_signature_new: boolean;
  auto_signature_reply: boolean;
  default_font: "Arial" | "Calibri" | "Georgia" | "Tahoma" | "Times New Roman" | "Verdana";
  default_font_size: "12px" | "14px" | "16px" | "18px";
  default_text_color: string;
  updated_at?: string | null;
};

export type MailAssistRequest = {
  project_id: number;
  reply_to_message_id: number | null;
  action: "compose" | "reply" | "improve" | "shorten" | "formal" | "friendly";
  tone: "business" | "neutral" | "friendly";
  instruction: string;
  subject: string;
  body: string;
};

export type MailAssistResult = {
  subject: string;
  body: string;
  notes: string;
  provider: string;
  model: string;
  policy_mode: string;
  requires_confirmation: boolean;
};
