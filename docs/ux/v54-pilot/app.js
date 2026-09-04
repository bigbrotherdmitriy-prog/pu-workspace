"use strict";
const UX = window.PilotUX;
const $ = id => document.getElementById(id);
const storageKey = "pu-v54-ux-synthetic-only-v1";
let state = UX.fresh();
let storageWarning = "";
try {
  const saved = sessionStorage.getItem(storageKey);
  if (saved) {
    const parsed = JSON.parse(saved);
    if (parsed.schema === 1 && Array.isArray(parsed.history)) {
      state = { ...state, ...parsed, pending: null, restored: true };
      state.notice = "Восстановлено только demo-состояние вкладки. Mutation не повторялась. В приложении требуется серверная перепроверка, IR-06.";
    }
  }
} catch { storageWarning = "Хранилище вкладки недоступно: после reload demo начнётся заново."; }
const escape = value => String(value).replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
let dialogSnapshot = null;
let dialogKind = "create";
let returnFocus = null;
function persist() {
  try { sessionStorage.setItem(storageKey, JSON.stringify(state)); }
  catch { storageWarning = "Хранилище вкладки недоступно: reload не сохранит demo."; }
}
function act(type, value) { state = UX.reduce(state, type, value); persist(); render(); }
const statuses = {
  review: ["Ожидает подтверждения", "Задача ещё не создана. Проверка контекста или срока не разрешает исполнение."],
  queued: ["Ожидает выполнения", "Разрешение точной версии принято в demo. Задача ещё не создана; постановка job и бизнес-результат — разные события."],
  running: ["Выполняется", "Дождитесь проверенного результата. Не повторяйте создание задачи."],
  succeeded: ["Создана задача", "Подтверждение результата: Task T7 + APPLIED receipt R25 + запись истории (вымышленный результат)."],
  failed: ["Ошибка без эффекта", "Demo предполагает подтверждённый rollback. Задача и receipt не созданы. Без серверного доказательства так писать нельзя, IR-07."],
  unknown: ["Результат неизвестен", "Job завершён, но подтверждённого бизнес-результата нет. Не создавайте задачу повторно. Нужна сверка Task/receipt; IR-07."],
  cancel_review: ["Отмена ожидает отдельного разрешения", "Исходная задача существует. Создание осталось успешным; отмена ещё не выполнена."],
  cancel_queued: ["Отмена ожидает выполнения", "Новое разрешение получено. Исходная задача пока assigned."],
  cancelled: ["Задача отменена", "Отдельное внутреннее действие. Первое создание и его receipt сохранены."]
};
function render() {
  const readable = state.source === "fresh";
  $("project").value = state.project;
  $("source").value = state.source;
  $("notice").textContent = [state.notice, storageWarning].filter(Boolean).join(" ");
  $("flow").hidden = state.project !== "4"; $("other").hidden = state.project === "4";
  $("source-status").textContent = ({fresh:"Актуален • demo",stale:"Устарел",unavailable:"Недоступен",revoked:"Доступ отозван",unknown:"Не удалось проверить"})[state.source];
  // No restricted quote remains in DOM, title, aria-label, or tooltip.
  $("fragment").replaceChildren();
  if (readable) {
    const location = document.createElement("p"); location.className = "muted";
    location.textContent = "UX-fixture · Акт-демо.pdf, страница 1, пункт 2 · IR-02";
    const quote = document.createElement("blockquote");
    quote.textContent = "Просим предоставить исправленный акт до 10 сентября 2026 года.";
    $("fragment").append(location, quote);
  } else {
    const warning = document.createElement("p"); warning.className = "warning";
    warning.textContent = "Фрагмент скрыт: актуальность или доступ не подтверждены. Нельзя продолжать по старой цитате.";
    $("fragment").append(warning);
  }
  $("fact").textContent = readable ? "Извлечённый факт: в источнике указана дата 10.09.2026. Это ещё не подтверждённый DeadlineClaim." : "Извлечённый фрагмент сейчас недоступен.";
  $("evidence-state").textContent = state.evidence ? "Evidence проверено отдельно • Мария (demo)" : "Evidence не проверено. Confidence не заменяет решение человека.";
  $("evidence").disabled = !UX.usable(state) || state.evidence;
  $("context-state").textContent = state.context ? "Связи подтверждены • Мария (demo)" : state.pending ? "Ожидается ответ подтверждения…" : "AI-гипотеза, не назначение по активному проекту.";
  $("context").disabled = !UX.usable(state) || !state.evidence || state.context || !!state.pending;
  $("claim-state").textContent = state.claim ? "Срок проверен отдельно • Мария (demo)" : "Срок не проверен. Проверьте evidence и контекст.";
  $("claim").disabled = !UX.usable(state) || !state.evidence || !state.context || state.claim;
  $("title").value = state.title; $("assignee").value = state.assignee;
  $("title").disabled = $("assignee").disabled = UX.frozen(state) || !UX.usable(state);
  $("revision").textContent = "Версия предложения r" + state.revision + " · изменение любого поля требует нового разрешения.";
  $("task-gate").textContent = UX.ready(state) ? "Проверки пройдены в demo. Сверьте точный состав задачи." : "Недостаточно подтверждений: нужны актуальное evidence, контекст и отдельная проверка срока.";
  $("open-approval").disabled = !UX.ready(state) || UX.frozen(state) || !state.title.trim();
  $("result-heading").textContent = statuses[state.status][0];
  $("result-detail").textContent = statuses[state.status][1];
  $("job").textContent = "Технический job: " + state.job + ". Процент не предоставлен.";
  $("receipts").replaceChildren();
  for (const line of [state.task, state.receipt && "Создание: " + state.receipt, state.cancelReceipt && "Отмена: " + state.cancelReceipt].filter(Boolean)) {
    const p = document.createElement("p"); p.textContent = line; $("receipts").append(p);
  }
  $("cancel").hidden = !["succeeded", "cancel_review"].includes(state.status);
  $("cancel").disabled = !UX.ready(state);
  $("history").replaceChildren();
  for (const row of state.history) {
    const li = document.createElement("li"); li.textContent = row.label; $("history").append(li);
  }
  for (const [id, done] of [["context",state.context],["claim",state.claim],["task",!!state.approval],["result",!!state.receipt]]) {
    $("step-" + id).classList.toggle("done", done);
  }
  const allowed = {
    hold: UX.usable(state) && state.evidence && !state.context && !state.pending,
    release: !!state.pending,
    start: state.status === "queued" && UX.ready(state) && !!state.approval,
    complete: state.status === "running", no_effect: state.status === "running", unknown: state.status === "running",
    cancel_complete: state.status === "cancel_queued" && UX.ready(state)
  };
  document.querySelectorAll("[data-event]").forEach(button => { button.disabled = Object.hasOwn(allowed, button.dataset.event) && !allowed[button.dataset.event]; });
}
function openDialog(kind) {
  dialogKind = kind; returnFocus = document.activeElement;
  dialogSnapshot = kind === "create" ? UX.fingerprint(state) : "T7/r1";
  $("dialog-heading").textContent = kind === "create" ? "Разрешить задачу · r" + state.revision : "Отдельно разрешить отмену · T7/r1";
  $("approval-body").innerHTML = '<dl><dt>Название</dt><dd>' + escape(state.title) +
    '</dd><dt>Исполнитель</dt><dd>' + escape(state.assignee) +
    '</dd><dt>Срок</dt><dd>10.09.2026 · без времени · Europe/Moscow</dd><dt>Контекст</dt><dd>Альфа / ГК-01</dd><dt>Основание</dt><dd>E16/r1 → S13 / V15/r1; Claim C17/r1</dd></dl>' +
    (kind === "create" ? '<p>Последствия: одна внутренняя Task, TaskHistory, receipt и audit. Никаких писем, оплат или изменения договора.</p>' :
      '<p>Последствия: T7 assigned → cancelled; новая история и отдельный receipt. Первое действие и его история останутся.</p>') +
    '<details><summary>Техническая привязка (demo)</summary><p class="hint">Мария — синтетический проверяющий. Реальные роли/expiry и server envelope SHA-256 не подключены (IR-05). Snapshot ниже — локальный ключ сравнения, НЕ криптографический seal.</p><code>' +
    escape(dialogSnapshot) + '</code></details>';
  $("dialog-error").textContent = ""; $("approval").showModal(); $("dismiss").focus();
}
function closeDialog() { $("approval").close(); if (returnFocus && !returnFocus.disabled) returnFocus.focus(); else $("main").focus(); }
$("evidence").onclick = () => act("evidence");
$("context").onclick = () => act("context");
$("claim").onclick = () => act("claim");
$("project").onchange = e => act("project", e.target.value);
$("return").onclick = () => act("project", "4");
$("source").onchange = e => act("source", e.target.value);
function edit() { act("edit", { title: $("title").value, assignee: $("assignee").value }); }
$("title").onchange = edit; $("assignee").onchange = edit;
$("open-approval").onclick = () => openDialog("create");
$("cancel").onclick = () => { act("cancel"); openDialog("cancel"); };
$("dismiss").onclick = closeDialog;
$("approval").addEventListener("cancel", e => { e.preventDefault(); closeDialog(); });
$("approve").onclick = () => {
  act(dialogKind === "create" ? "approve" : "cancel_approve", dialogSnapshot);
  if (state.notice) $("dialog-error").textContent = state.notice;
  else closeDialog();
};
document.querySelectorAll("[data-event]").forEach(button => { button.onclick = () => act(button.dataset.event); });
$("reload").onclick = () => location.reload();
$("reset").onclick = () => { state = UX.fresh(); persist(); render(); $("main").focus(); };
render();
