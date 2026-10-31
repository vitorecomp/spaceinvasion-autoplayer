import React, { Profiler } from "react";
import "./utils/formatters";
import { ConsoleProvider, useActivePage } from "./store";
import { AppHeader } from "./components/layout/AppHeader";
import { AppSidebar } from "./components/layout/AppSidebar";
import { HelpAlertModal } from "./components/modals/HelpAlertModal";
import { NotificationCenterModal } from "./components/modals/NotificationCenterModal";
import { DashboardView } from "./features/dashboard";
import { OperationsView } from "./features/operations";
import { ActionGraphView } from "./features/action-graph";
import { BuildingsView } from "./features/buildings";
import { ResearchView } from "./features/research";
import { CombatView } from "./features/combat";
import { FleetControlView } from "./features/fleet-control";
import { SectorsView } from "./features/sectors";
import { EspionageView } from "./features/espionage";
import { ColoniesView } from "./features/colonies";
import { AttacksView } from "./features/attacks";
import { RecyclersView } from "./features/recyclers";
import { RankingView } from "./features/ranking";
import { ConfigView } from "./features/config";
import type { PageKey } from "./types/api";

export const CONSOLE_PAGE_ORDER: readonly PageKey[] = [
  "dashboard",
  "operations",
  "action-graph",
  "buildings",
  "research",
  "combat",
  "fleet-control",
  "sectors",
  "espionage",
  "colonies",
  "attacks",
  "recyclers",
  "ranking",
  "config",
];

export interface AppProps {
  disableInitialFetch?: boolean;
  onPageRender?: React.ProfilerOnRenderCallback;
}

const ActivePageStage: React.FC<{ onPageRender?: React.ProfilerOnRenderCallback }> = ({
  onPageRender,
}) => {
  const activePage = useActivePage();

  let pageNode: React.ReactNode;
  switch (activePage) {
    case "operations":
      pageNode = <OperationsView />;
      break;
    case "action-graph":
      pageNode = <ActionGraphView />;
      break;
    case "buildings":
      pageNode = <BuildingsView />;
      break;
    case "research":
      pageNode = <ResearchView />;
      break;
    case "combat":
      pageNode = <CombatView />;
      break;
    case "fleet-control":
      pageNode = <FleetControlView />;
      break;
    case "sectors":
      pageNode = <SectorsView />;
      break;
    case "espionage":
      pageNode = <EspionageView />;
      break;
    case "colonies":
      pageNode = <ColoniesView />;
      break;
    case "attacks":
      pageNode = <AttacksView />;
      break;
    case "recyclers":
      pageNode = <RecyclersView />;
      break;
    case "ranking":
      pageNode = <RankingView />;
      break;
    case "config":
      pageNode = <ConfigView />;
      break;
    case "dashboard":
    default:
      pageNode = <DashboardView />;
      break;
  }

  return (
    <main className="view-stage" id="view-stage">
      {onPageRender ? (
        <Profiler id={`page:${activePage}`} onRender={onPageRender}>
          {pageNode}
        </Profiler>
      ) : (
        pageNode
      )}
    </main>
  );
};

export const App: React.FC<AppProps> = ({ disableInitialFetch = false, onPageRender }) => {
  return (
    <ConsoleProvider disableInitialFetch={disableInitialFetch}>
      <div className="hud-canvas">
        <div className="app-layout">
          <AppSidebar />
          <div className="main-column">
            <AppHeader />
            <ActivePageStage onPageRender={onPageRender} />
          </div>
        </div>
        <HelpAlertModal />
        <NotificationCenterModal />
      </div>
    </ConsoleProvider>
  );
};

export default App;
