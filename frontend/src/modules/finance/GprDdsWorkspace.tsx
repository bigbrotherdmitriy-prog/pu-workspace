import type { ReactNode } from "react";
import { CalendarRange, Wallet } from "lucide-react";

export type GprDdsTab = "gpr" | "dds";

type Props = {
  tab: GprDdsTab;
  onTabChange: (tab: GprDdsTab) => void;
  gpr: ReactNode;
  dds: ReactNode;
};

/** One project-wide work surface. Both panels stay mounted so filters and selections survive tab changes. */
export function GprDdsWorkspace({ tab, onTabChange, gpr, dds }: Props) {
  return <div className="gpr-dds-unified">
    <header className="gpr-dds-unified-head">
      <div><span className="eyebrow">ЕДИНЫЙ ПЛАН ПРОЕКТА</span><h1>ГПР и ДДС</h1></div>
      <div className="gpr-dds-primary-tabs" role="tablist" aria-label="ГПР и ДДС">
        <button type="button" role="tab" aria-selected={tab === "gpr"} className={tab === "gpr" ? "active" : ""} onClick={() => onTabChange("gpr")}><CalendarRange /> ГПР</button>
        <button type="button" role="tab" aria-selected={tab === "dds"} className={tab === "dds" ? "active" : ""} onClick={() => onTabChange("dds")}><Wallet /> ДДС</button>
      </div>
    </header>
    <div role="tabpanel" aria-label="ГПР" hidden={tab !== "gpr"}>{gpr}</div>
    <div role="tabpanel" aria-label="ДДС" hidden={tab !== "dds"}>{dds}</div>
  </div>;
}
