import React from "react";
import "./utils/formatters";
import { ConsoleProvider } from "./context/ConsoleContext";
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
import { LogsView } from "./features/logs";

export interface AppProps {
  disableInitialFetch?: boolean;
}

export const App: React.FC<AppProps> = ({ disableInitialFetch = false }) => {
  return (
    <ConsoleProvider disableInitialFetch={disableInitialFetch}>
      <div className="hud-canvas">
        <div className="app-layout">
          <AppSidebar />
          <div className="main-column">
            <AppHeader />
            <main className="view-stage" id="view-stage">
              <DashboardView />
              <OperationsView />
              <ActionGraphView />
              <BuildingsView />
              <ResearchView />
              <CombatView />
              <FleetControlView />
              <SectorsView />
              <EspionageView />
              <ColoniesView />
              <AttacksView />
              <RecyclersView />
              <RankingView />
              <ConfigView />
              <LogsView />
            </main>
          </div>
        </div>
        <HelpAlertModal />
        <NotificationCenterModal />
      </div>
    </ConsoleProvider>
  );
};

export default App;
