import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { FinanceForecastPanel } from "./FinanceForecastPanel";
import { financeForecastOverviewFixture as overviewFixture } from "./financeForecastFixtures";

afterEach(cleanup);

describe("FinanceForecastPanel", () => {
  it("renders explicit basis, reliability, cash gap and both rollups", () => {
    render(<FinanceForecastPanel overview={overviewFixture()} projectId={7} />);
    expect(screen.getByRole("heading", { name: "План → обязательства → факт → прогноз" })).toBeInTheDocument();
    expect(screen.getByText("Надёжно")).toBeInTheDocument();
    expect(screen.getByText("Первая дата: 2026-10-04")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "По договорам" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "По структуре ГПР / WBS" })).toBeInTheDocument();
    expect(screen.getByText("Корпус")).toBeInTheDocument();
    expect(screen.getByText("Сводный узел")).toBeInTheDocument();
    expect(screen.getByText(/Автоплатежи, проводки и автоматическая конвертация отключены/)).toBeInTheDocument();
  });

  it("shows decision-required state without invented confidence percentages", () => {
    const raw = overviewFixture();
    raw.summary.financial_totals_reliable = false;
    raw.decision_requirements = [{ code: "retention_treatment", decision_by: "OWNER", message: "Выберите правило удержания" }];
    const { container } = render(<FinanceForecastPanel overview={raw} projectId={7} />);
    const decisions = screen.getByRole("region", { name: "Требуемые решения" });
    expect(within(decisions).getByText("OWNER")).toBeInTheDocument();
    expect(within(decisions).getByText("Выберите правило удержания")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\d+%/);
  });

  it("fails closed without leaking invalid values", () => {
    const raw = overviewFixture(); raw.summary.budget_actual = Number.NaN;
    render(<FinanceForecastPanel overview={raw} projectId={7} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Финансовый прогноз скрыт");
    expect(screen.queryByText("NaN")).not.toBeInTheDocument();
  });

  it("does not render controls that could create payments", () => {
    render(<FinanceForecastPanel overview={overviewFixture()} projectId={7} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
