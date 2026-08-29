import { useState } from "react";
import { api } from "../api/client";


export function Login({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit() {
    try {
      const data = await api<{ access_token: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      sessionStorage.setItem("pu_token", data.access_token);
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
      <label>Email<input value={email} onChange={(event) => setEmail(event.target.value)} type="email" /></label>
      <label>Пароль<input value={password} onChange={(event) => setPassword(event.target.value)} type="password" onKeyDown={(event) => event.key === "Enter" && submit()} /></label>
      <button onClick={submit}>Войти</button>
      {error && <div className="error">{error}</div>}
      <a href="/">Открыть прежний интерфейс</a>
    </div>
  </div>;
}
