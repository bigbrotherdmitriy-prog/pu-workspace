import { useState } from "react";
import { api } from "../api/client";
import { completeExplicitLogin, flushPendingRevocations, isLocallySignedOut, signoutGeneration } from './localSignout';


export function Login({ onDone, notice = '' }: { onDone: () => void; notice?: string }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit() {
    try {
      const started = signoutGeneration();
      await flushPendingRevocations();
      const result = await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      completeExplicitLogin(result, started);
      onDone();
    } catch (failure) {
      setError((failure as Error).message);
    }
  }

  return <div className="login-page">
    <div className="login-card">
      <div className="brand-mark">PU</div>
      <h1>Вход в PU Workspace</h1>
      <p>Единое рабочее пространство проектов и документов</p>
      {notice && <p role="status">{notice}</p>}
      <label>Email<input value={email} onChange={(event) => setEmail(event.target.value)} type="email" /></label>
      <label>Пароль<input value={password} onChange={(event) => setPassword(event.target.value)} type="password" onKeyDown={(event) => event.key === "Enter" && submit()} /></label>
      <button onClick={submit}>Войти</button>
      {error && <div className="error">{error}</div>}
      {!isLocallySignedOut() && <a href="/">Открыть прежний интерфейс</a>}
    </div>
  </div>;
}
