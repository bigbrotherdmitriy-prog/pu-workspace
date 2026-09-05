import React from "react";

type State = { failed: boolean };

export class AppErrorBoundary extends React.Component<React.PropsWithChildren, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  private async recover() {
    try {
      if ("caches" in window) {
        const keys = await window.caches.keys();
        await Promise.all(keys.filter((key) => key.startsWith("pu-workspace-")).map((key) => window.caches.delete(key)));
      }
      if ("serviceWorker" in navigator) {
        const registrations = await navigator.serviceWorker.getRegistrations();
        await Promise.all(
          registrations
            .filter((registration) => registration.scope.includes("/new/"))
            .map((registration) => registration.unregister()),
        );
      }
    } finally {
      window.location.reload();
    }
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="app-recovery" role="alert">
        <div>
          <span>PU WORKSPACE</span>
          <h1>Интерфейс нужно обновить</h1>
          <p>Версия приложения изменилась во время работы. Ваши проекты и документы сохранены.</p>
          <button type="button" onClick={() => void this.recover()}>Обновить приложение</button>
        </div>
      </main>
    );
  }
}
