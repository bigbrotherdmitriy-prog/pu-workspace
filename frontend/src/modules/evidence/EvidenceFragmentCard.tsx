import "./EvidenceFragmentCard.css";
import { toEvidenceFragmentViewModel } from "./evidenceReadModel";

type EvidenceFragmentCardProps = {
  input: unknown;
};

function Detail({ term, children }: { term: string; children: React.ReactNode }) {
  return <div className="evidence-fragment-card__detail"><dt>{term}</dt><dd>{children}</dd></div>;
}

export function EvidenceFragmentCard({ input }: EvidenceFragmentCardProps) {
  const model = toEvidenceFragmentViewModel(input);

  if (!model.metadataVisible) {
    return <article className="evidence-fragment-card evidence-fragment-card--hidden" role="status" aria-live="polite" aria-atomic="true">
      <header className="evidence-fragment-card__header">
        <div><p className="evidence-fragment-card__eyebrow">EVIDENCE</p><h2>Доказательство недоступно</h2></div>
        <span className="evidence-fragment-card__badge evidence-fragment-card__badge--unavailable">{model.statusLabel}</span>
      </header>
      <p className="evidence-fragment-card__notice">{model.reasonLabel}</p>
    </article>;
  }

  return <article className={`evidence-fragment-card evidence-fragment-card--${model.status}`} role="status" aria-live="polite" aria-atomic="true">
    <header className="evidence-fragment-card__header">
      <div><p className="evidence-fragment-card__eyebrow">EVIDENCE · EXACT VERSION</p><h2>Основание вывода</h2></div>
      <span className={`evidence-fragment-card__badge evidence-fragment-card__badge--${model.status}`}>{model.statusLabel}</span>
    </header>

    {model.historical && <p className="evidence-fragment-card__historical">
      Историческое доказательство: фрагмент относится к архивной версии, а не к текущему источнику.
    </p>}

    <section className="evidence-fragment-card__section">
      <h3>Источник и exact pins</h3>
      <dl className="evidence-fragment-card__grid">
        <Detail term="Провайдер">{model.source.provider}</Detail>
        <Detail term="Аккаунт">{model.source.account}</Detail>
        <Detail term="Namespace">{model.source.namespace}</Detail>
        <Detail term="Origin project">{model.source.originProject} · не подтверждённое назначение</Detail>
        <Detail term="Evidence"><code>{model.pins.evidence}</code></Detail>
        <Detail term="Source"><code>{model.pins.source}</code></Detail>
        <Detail term="Source version"><code>{model.pins.sourceVersion}</code></Detail>
      </dl>
    </section>

    <section className="evidence-fragment-card__section">
      <h3>Локатор</h3>
      <p className="evidence-fragment-card__locator">{model.locator.label}</p>
      {!model.locator.preciseNavigation && <p className="evidence-fragment-card__navigation-warning">
        Точная навигация недоступна: связь с областью оригинала не доказана.
      </p>}
    </section>

    <section className="evidence-fragment-card__section">
      <h3>Фрагмент</h3>
      <p className="evidence-fragment-card__media">{model.fragment.mediaType}</p>
      <blockquote>{model.fragment.excerpt}</blockquote>
    </section>

    <section className="evidence-fragment-card__section evidence-fragment-card__derived">
      <div><h3>Извлечённый факт</h3><p>{model.extractedFact ?? "Не извлечён"}</p></div>
      <div><h3>Вывод AI</h3><p>{model.aiConclusion ?? "Нет вывода"}</p><small>Вывод AI не является фактом или решением человека.</small></div>
    </section>

    <section className="evidence-fragment-card__section">
      <h3>Извлечение</h3>
      <dl className="evidence-fragment-card__grid">
        <Detail term="Extractor">{model.extraction.extractor}</Detail>
        <Detail term="Метод">{model.extraction.method}</Detail>
        <Detail term="Модель">{model.extraction.model ?? "Не использовалась"}</Detail>
        <Detail term="Prompt">{model.extraction.prompt ?? "Не использовался"}</Detail>
        <Detail term="Confidence">{model.extraction.confidence}</Detail>
        <Detail term="Калибровка">{model.extraction.calibration ?? "Не указана"}</Detail>
      </dl>
      <p className="evidence-fragment-card__confidence-note">Уверенность — это оценка извлечения, а не гарантия истинности.</p>
    </section>

    <section className="evidence-fragment-card__section">
      <h3>Проверка человеком</h3>
      <p className="evidence-fragment-card__assessment"><strong>{model.assessment.label}</strong></p>
      <dl className="evidence-fragment-card__grid">
        <Detail term="Проверяющий">{model.assessment.reviewer ?? "Не назначен"}</Detail>
        <Detail term="Время проверки">{model.assessment.reviewedAt ?? "Нет"}</Detail>
        <Detail term="Версия assessment">r{model.assessment.version}</Detail>
      </dl>
      {model.assessment.verification === "unverified" && <p className="evidence-fragment-card__notice">
        Фрагмент доступен только для проверки. Confidence не заменяет решение человека.
      </p>}
    </section>
  </article>;
}
