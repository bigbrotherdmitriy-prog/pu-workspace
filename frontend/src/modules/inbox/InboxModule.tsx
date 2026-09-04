import type { ReactNode } from "react";
import { Bot, Mail, RefreshCw } from "lucide-react";

type Props = {
  collapsed: boolean;
  mode: "mail" | "secretary";
  attentionCount: number;
  syncing: boolean;
  onSync: () => void;
  children: ReactNode;
};

export function InboxModule({ collapsed, mode, attentionCount, syncing, onSync, children }: Props) {
  const isMail = mode === "mail";
  return <section className={`secretary-overlay ${collapsed ? "collapsed" : ""}`}>
    <div className="secretary-page">
      {!isMail && <section className="card secretary-intro">
        <div className="source-icon">{isMail ? <Mail /> : <Bot />}</div>
        <div>
          <h2>{isMail ? "Входящие письма" : "Входящие AI Secretary"}</h2>
          <p>{isMail
            ? "Входящие и исходящие сообщения: AI-сводка, задачи, черновики и проверка выполнения задач по отправленным ответам."
            : "Источник → контекст проекта и договора → сводка → предложения задач и ответа. Ничего внешнего не создаётся без подтверждения."}</p>
        </div>
        <span>{attentionCount} требуют внимания</span>
        {isMail && <button className="inbox-sync" onClick={onSync} disabled={syncing}>
          <RefreshCw /> {syncing ? "Получаю…" : "Получить новые"}
        </button>}
      </section>}
      {children}
    </div>
  </section>;
}
