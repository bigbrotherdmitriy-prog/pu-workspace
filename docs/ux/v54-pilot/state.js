/* Local UX simulator, NOT a backend DTO, approval store or authorization engine. */
(function (root) {
  "use strict";
  const fresh = () => ({
    schema: 1, project: "4", requestEpoch: 0, source: "fresh", evidence: false, context: false,
    claim: false, revision: 1, title: "Предоставить исправленный акт",
    assignee: "Иван • инженер проекта", approval: null, status: "review",
    receipt: null, cancelReceipt: null, cancelApproval: null, cancelBlocked: false, task: null, pending: null, sequence: 0,
    history: [], notice: "", job: "Не поставлено", restored: false
  });
  const usable = s => s.source === "fresh" && s.project === "4";
  const ready = s => usable(s) && s.evidence && s.context && s.claim;
  const fingerprint = s => JSON.stringify([s.revision, s.title, s.assignee, "4", "5", "2026-09-10"]);
  const blocked = s => !ready(s) || s.approval !== fingerprint(s);
  const frozen = s => ["queued", "running", "unknown", "succeeded", "cancel_review", "cancel_queued", "cancelled"].includes(s.status);
  function event(s, label) { s.history.push({ sequence: ++s.sequence, label }); }
  function reduce(previous, type, value) {
    const s = JSON.parse(JSON.stringify(previous));
    s.notice = "";
    switch (type) {
      case "project":
        s.project = value; s.requestEpoch++;
        s.notice = "Изменён только просмотр. Контекст письма не переназначен.";
        break;
      case "evidence":
        if (usable(s) && !s.evidence) { s.evidence = true; event(s, "Доказательство E16/r1 проверено • Мария (demo)"); }
        break;
      case "context":
        if (usable(s) && s.evidence && !s.context) {
          s.context = true; event(s, "Контекст подтверждён: Альфа / ГК-01 • Мария (demo)");
        }
        break;
      case "hold":
        if (usable(s) && s.evidence && !s.context && !s.pending) {
          s.pending = { project: s.project, revision: s.revision, epoch: s.requestEpoch };
          s.notice = "Demo: ответ контекста удерживается. Можно сменить проект.";
        }
        break;
      case "release":
        if (s.pending) {
          if (s.project !== s.pending.project || s.revision !== s.pending.revision || s.requestEpoch !== s.pending.epoch) {
            s.notice = "Поздний ответ не применён к этому просмотру. Вернитесь к письму и перечитайте состояние.";
          } else { s.context = true; event(s, "Контекст подтверждён после задержанного ответа (demo)"); }
          s.pending = null;
        }
        break;
      case "claim":
        if (ready({ ...s, claim: true }) && !s.claim) {
          s.claim = true; event(s, "DeadlineClaim C17/r1: 10.09.2026 проверен отдельно • Мария (demo)");
        }
        break;
      case "edit":
        if (!frozen(s) && usable(s)) {
          s.title = value.title; s.assignee = value.assignee;
          s.revision++; s.approval = null; s.status = "review";
          event(s, "Новая версия задачи r" + s.revision + "; прежнее разрешение неприменимо");
        }
        break;
      case "approve":
        if (ready(s) && value === fingerprint(s) && !frozen(s) && s.title.trim()) {
          s.approval = value; s.status = "queued"; s.job = "Ожидает постановки";
          event(s, "Разрешена точная версия r" + s.revision + " • Мария (demo)");
        } else s.notice = "Версия или условия изменились. Откройте подтверждение заново.";
        break;
      case "source":
        s.source = value;
        if (value !== "fresh") {
          s.cancelApproval = null;
          if (s.status === "cancel_queued") s.status = "cancel_review";
          s.evidence = false;
          s.notice = "Источник требует проверки. Новое исполнение заблокировано.";
          if (!s.receipt && !["running", "unknown"].includes(s.status)) {
            s.approval = null; s.status = "review";
          }
        }
        break;
      case "conflict":
        if (s.receipt && !s.cancelReceipt) {
          s.cancelBlocked = true; s.cancelApproval = null;
          if (s.status === "cancel_queued") s.status = "cancel_review";
        }
        if (!s.receipt && !["running", "unknown"].includes(s.status)) {
          s.approval = null; s.status = "review"; s.revision++;
        }
        s.notice = "409: данные изменились. Сравните новую версию и подтвердите заново. Автоповтора нет.";
        break;
      case "revoke":
      case "expire":
        s.approval = null;
        s.cancelApproval = null;
        if (s.status === "cancel_queued") s.status = "cancel_review";
        if (!s.receipt && !["running", "unknown"].includes(s.status)) s.status = "review";
        s.notice = (type === "revoke" ? "Разрешение отозвано." : "Срок разрешения истёк.") +
          (s.receipt ? " Созданная задача не отменяется." : " Необходимо новое разрешение; при начатом выполнении сначала уточняется результат.");
        event(s, type === "revoke" ? "Approval отозван (demo)" : "Approval истёк (demo, часы сервера не подключены)");
        break;
      case "start":
        if (s.status === "queued" && !blocked(s)) { s.status = "running"; s.job = "running"; }
        else s.notice = "Выполнение не начато: проверьте контекст, срок, evidence и разрешение.";
        break;
      case "complete":
        if (s.status === "running") {
          s.status = "succeeded"; s.job = "completed";
          s.receipt = "R25 • APPLIED"; s.task = "T7 • assigned";
          event(s, "Task T7 создана • receipt R25/APPLIED • внутреннее действие (demo)");
        }
        break;
      case "no_effect":
        if (s.status === "running") {
          s.status = "failed"; s.job = "failed"; s.approval = null;
          event(s, "Подтверждена ошибка без эффекта (demo); receipt отсутствует");
        }
        break;
      case "unknown":
        if (s.status === "running") { s.status = "unknown"; s.job = "completed"; }
        break;
      case "cancel":
        if (s.status === "succeeded" && usable(s)) s.status = "cancel_review";
        break;
      case "cancel_approve":
        if (s.status === "cancel_review" && ready(s) && !s.cancelBlocked && value === "T7/r1") {
          s.status = "cancel_queued"; s.cancelApproval = value;
          event(s, "Отдельное разрешение отмены T7/r1 • Мария (demo)");
        } else s.notice = "Отмена не разрешена: версия задачи или доступ требуют проверки.";
        break;
      case "cancel_complete":
        if (s.status === "cancel_queued" && ready(s) && !s.cancelBlocked && s.cancelApproval === "T7/r1") {
          s.status = "cancelled"; s.cancelReceipt = "R26 • APPLIED"; s.task = "T7 • cancelled";
          event(s, "Task T7 отменена • отдельный receipt R26/APPLIED (demo)");
        }
        break;
    }
    return s;
  }
  const api = { fresh, reduce, ready, usable, fingerprint, frozen };
  if (typeof module !== "undefined") module.exports = api;
  else root.PilotUX = api;
})(typeof window !== "undefined" ? window : globalThis);
