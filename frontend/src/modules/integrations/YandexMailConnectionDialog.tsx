import { FormEvent, useEffect, useRef, useState } from "react";
import { Mail, ShieldCheck, X } from "lucide-react";
import "./yandex-mail-connection.css";

type Props = {
  open: boolean;
  initialEmail: string;
  busy: boolean;
  onClose: () => void;
  onSubmit: (email: string, appPassword: string) => Promise<void>;
};

export function YandexMailConnectionDialog({ open, initialEmail, busy, onClose, onSubmit }: Props) {
  const [email, setEmail] = useState(initialEmail);
  const passwordRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setEmail(initialEmail);
      if (passwordRef.current) passwordRef.current.value = "";
    }
  }, [open, initialEmail]);

  if (!open) return null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const appPassword = passwordRef.current?.value ?? "";
    await onSubmit(email.trim(), appPassword);
    if (passwordRef.current) passwordRef.current.value = "";
  }

  return <div className="yandex-mail-backdrop" role="presentation">
    <form className="yandex-mail-dialog" role="dialog" aria-modal="true" aria-labelledby="yandex-mail-title" onSubmit={(event) => void submit(event)}>
      <header>
        <span><Mail /></span>
        <div><small>Только чтение</small><h2 id="yandex-mail-title">Подключить Яндекс Почту</h2></div>
        <button type="button" className="icon-button" aria-label="Закрыть" onClick={onClose} disabled={busy}><X /></button>
      </header>
      <p>PU Workspace получит до 25 последних писем по IMAP. Письма не удаляются, не помечаются прочитанными и не отправляются.</p>
      <label>
        <span>Корпоративный адрес</span>
        <input type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} required placeholder="name@company.ru" />
      </label>
      <label>
        <span>Пароль приложения «Почта»</span>
        <input ref={passwordRef} name="app_password" type="password" autoComplete="new-password" required minLength={8} placeholder="Не основной пароль" />
      </label>
      <div className="yandex-mail-security"><ShieldCheck /><span>Пароль проверяется у Яндекса и хранится в зашифрованном виде. SMTP и AUTO не включаются.</span></div>
      <footer>
        <button type="button" className="secondary" onClick={onClose} disabled={busy}>Отмена</button>
        <button type="submit" disabled={busy}>{busy ? "Проверяю…" : "Подключить"}</button>
      </footer>
    </form>
  </div>;
}
