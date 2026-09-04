import { useEffect, useState } from "react";
import { Save, Settings, XCircle } from "lucide-react";
import type { MailSettings } from "./types";
import { RichTextEditor } from "./RichTextEditor";

type Props = {
  settings: MailSettings;
  busy: boolean;
  onClose: () => void;
  onSave: (settings: MailSettings) => Promise<void> | void;
};

export function MailSettingsDialog({ settings, busy, onClose, onSave }: Props) {
  const [draft, setDraft] = useState(settings);
  useEffect(() => setDraft(settings), [settings]);

  return <div className="mail-confirm-backdrop" role="presentation">
    <section className="mail-settings-dialog" role="dialog" aria-modal="true" aria-labelledby="mail-settings-title">
      <header>
        <div><Settings /><div><span className="eyebrow">ПОЧТА</span><h2 id="mail-settings-title">Настройки и подпись</h2></div></div>
        <button type="button" aria-label="Закрыть настройки" onClick={onClose}><XCircle /></button>
      </header>
      <div className="mail-settings-body">
        <label>Отображаемое имя<input value={draft.display_name} onChange={(event) => setDraft({ ...draft, display_name: event.target.value })} placeholder="Имя и должность" /></label>
        <div className="mail-settings-row">
          <label>Шрифт по умолчанию<select value={draft.default_font} onChange={(event) => setDraft({ ...draft, default_font: event.target.value as MailSettings["default_font"] })}>{['Arial', 'Calibri', 'Georgia', 'Tahoma', 'Times New Roman', 'Verdana'].map((font) => <option key={font}>{font}</option>)}</select></label>
          <label>Размер<select value={draft.default_font_size} onChange={(event) => setDraft({ ...draft, default_font_size: event.target.value as MailSettings["default_font_size"] })}><option>12px</option><option>14px</option><option>16px</option><option>18px</option></select></label>
          <label>Цвет<input type="color" value={draft.default_text_color} onChange={(event) => setDraft({ ...draft, default_text_color: event.target.value })} /></label>
        </div>
        <div className="mail-signature-field">
          <strong>Подпись</strong>
          <small>Она добавляется в новое письмо или ответ согласно настройкам ниже.</small>
          <RichTextEditor value={draft.signature_html} onChange={(signature_html) => setDraft({ ...draft, signature_html })} font={draft.default_font} fontSize={draft.default_font_size} color={draft.default_text_color} ariaLabel="Подпись" />
        </div>
        <label className="mail-checkbox"><input type="checkbox" checked={draft.auto_signature_new} onChange={(event) => setDraft({ ...draft, auto_signature_new: event.target.checked })} />Добавлять подпись в новые письма</label>
        <label className="mail-checkbox"><input type="checkbox" checked={draft.auto_signature_reply} onChange={(event) => setDraft({ ...draft, auto_signature_reply: event.target.checked })} />Добавлять подпись в ответы и пересылки</label>
        <p className="mail-settings-note">Настройки сохраняются в вашей учётной записи PU Workspace. Подпись можно изменить перед отправкой каждого письма.</p>
      </div>
      <footer><button type="button" className="secondary" disabled={busy} onClick={onClose}>Отмена</button><button type="button" className="send" disabled={busy} onClick={() => void onSave(draft)}><Save />{busy ? "Сохраняю…" : "Сохранить настройки"}</button></footer>
    </section>
  </div>;
}
