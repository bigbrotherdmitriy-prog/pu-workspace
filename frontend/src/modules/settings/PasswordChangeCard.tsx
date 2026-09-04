import { useState } from "react";
import { api } from "../../api/client";


export function PasswordChangeCard({ onChanged }: { onChanged: () => void }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setError("");
    if (newPassword.length < 12) {
      setError("Новый пароль должен содержать не менее 12 символов");
      return;
    }
    if (newPassword !== confirmation) {
      setError("Новый пароль и подтверждение не совпадают");
      return;
    }
    setBusy(true);
    try {
      await api("/auth/change-password", {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      onChanged();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <section className="card">
    <div className="card-head">
      <div>
        <h2>Сменить пароль</h2>
        <p>После смены все активные сеансы будут завершены</p>
      </div>
    </div>
    <div className="form-grid">
      <label>
        Текущий пароль
        <input
          type="password"
          autoComplete="current-password"
          value={currentPassword}
          onChange={(event) => setCurrentPassword(event.target.value)}
        />
      </label>
      <label>
        Новый пароль
        <input
          type="password"
          autoComplete="new-password"
          value={newPassword}
          onChange={(event) => setNewPassword(event.target.value)}
        />
      </label>
      <label>
        Повторите новый пароль
        <input
          type="password"
          autoComplete="new-password"
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && void submit()}
        />
      </label>
      <button onClick={() => void submit()} disabled={busy || !currentPassword || !newPassword || !confirmation}>
        {busy ? "Сохраняем…" : "Сменить пароль"}
      </button>
      {error && <p className="error">{error}</p>}
    </div>
  </section>;
}
