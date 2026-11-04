export { isOnline } from "../../src/offline/connectivity";
import { startConnectivityMonitor } from "../../src/offline/connectivity";
export { synchronize } from "../../src/offline/syncEngine";
// Actual product components with explicit synthetic actor/project, no real account.
export * from "./offlineHarness";
import { createRoot, type Root } from "react-dom/client";
import { WorkLogModule } from "../../src/modules/work-log/WorkLogModule";
import { DefectsModule } from "../../src/modules/defects/DefectsModule";
import { resumeOfflineSession } from "../../src/offline/lifecycle";
let root: Root | undefined;
let stopMonitor: (() => void) | undefined;
export function mountSyntheticMobile(kind: "journal" | "defect") {
  resumeOfflineSession(1);
  stopMonitor ||= startConnectivityMonitor();
  const element = document.getElementById("synthetic-mobile") || document.body.appendChild(Object.assign(document.createElement("div"), { id: "synthetic-mobile" }));
  root ||= createRoot(element);
  root.render(kind === "journal" ? <WorkLogModule projectId={11} userId={1} /> : <DefectsModule projectId={11} userId={1} />);
}
