import React, { useMemo } from "react";
import { feedbackKindForError } from "../../api/httpClient";
import { SortableTh } from "../../components/SortableTh";
import { useConsole } from "../../context/ConsoleContext";
import {
  coordsSortKey,
  timestampSortKey,
  useTableSort,
  type SortColumnMap,
} from "../../hooks/useTableSort";
import {
  finiteNum,
  fmtDurationCountdown,
  fmtInt,
  fmtShortDateTime,
  formatResourcesInline,
} from "../../utils/formatters";

export interface AttacksViewProps {
  hidden?: boolean;
}

export const AttacksView: React.FC<AttacksViewProps> = ({ hidden }) => {
  const {
    activePage,
    activePlanet,
    atkData,
    refreshAttacksView,
    syncAllInboxFolders,
    showFeedback,
    apiCommand,
    uiPrefs,
    setUiPref,
  } = useConsole();

  const isHidden = hidden ?? activePage !== "attacks";
  const atkTab = uiPrefs.atkTab === "blocks" ? "blocks" : "battles";

  const sum = atkData?.summary || {};
  const rStats = atkData?.raid_stats || {};
  const battles: Array<Record<string, any>> = Array.isArray(atkData?.battles)
    ? atkData!.battles
    : [];
  const blocks: Array<Record<string, any>> = Array.isArray(atkData?.blocked_targets)
    ? atkData!.blocked_targets
    : [];
  const raids24 = Math.round(finiteNum(rStats.total_24h, 0));

  const battleSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "time" | "target" | "defender" | "outcome" | "rounds" | "loot" | "losses" | "debris"
    >
  >(
    () => ({
      time: {
        accessor: (b) => timestampSortKey(b.ts, b.msg_date_text),
        defaultDirection: "desc",
      },
      target: (b) => coordsSortKey(b.coords),
      defender: (b) => String(b.defender_name || b.subject || "").trim() || null,
      outcome: (b) => String(b.outcome || "draw").toLowerCase(),
      rounds: {
        accessor: (b) => finiteNum(b.rounds, null),
        defaultDirection: "desc",
      },
      loot: {
        accessor: (b) => finiteNum(b.loot?.spice_eq, 0),
        defaultDirection: "desc",
      },
      losses: {
        accessor: (b) =>
          finiteNum(b.attacker_losses?.spice_eq, 0) + finiteNum(b.defender_losses?.spice_eq, 0),
        defaultDirection: "desc",
      },
      debris: {
        accessor: (b) =>
          finiteNum(b.debris?.pig_iron, 0) + finiteNum(b.debris?.metal, 0),
        defaultDirection: "desc",
      },
    }),
    []
  );

  const blockSortCols = useMemo<
    SortColumnMap<
      Record<string, any>,
      "coords" | "owner" | "bashHits" | "cooldown" | "reason"
    >
  >(
    () => ({
      coords: (bt) => coordsSortKey(bt.coords),
      owner: (bt) => [bt.planet_name, bt.owner].filter(Boolean).join(" · ") || null,
      bashHits: {
        accessor: (bt) => finiteNum(bt.bash_hits, 0),
        defaultDirection: "desc",
      },
      cooldown: {
        accessor: (bt) => (bt.active_block ? finiteNum(bt.remaining_seconds, 0) : 0),
        defaultDirection: "desc",
      },
      reason: (bt) => String(bt.block_reason || "").trim() || null,
    }),
    []
  );

  const battleSort = useTableSort(battles, battleSortCols);
  const blockSort = useTableSort(blocks, blockSortCols);

  const handleSpyBlockTarget = async (coords: string) => {
    try {
      const res = await apiCommand({
        command: "spy",
        target_coords: coords,
        quantity: 1,
        planet_id: activePlanet?.planet_id,
        dry_run: false,
      });
      showFeedback(
        res.message || `Spy probe sent to ${coords}.`,
        res.executed ? "ok" : "warn"
      );
    } catch (err: any) {
      showFeedback(`Spy failed: ${err.message}`, feedbackKindForError(err));
    }
  };

  return (
    <section
      id="view-attacks"
      className="page-view"
      aria-label="Battle reports, plunder, and bash protection status"
      hidden={isHidden}
    >
      <div className="combat-top-cards">
        <div className="kpi-card">
          <span className="kpi-label tabular">Recorded Battles</span>
          <strong className="kpi-value tabular" id="atk-kpi-total">
            {`${fmtInt(sum.total_battles ?? battles.length)} Battles`}
          </strong>
          <span className="kpi-sub tabular" id="atk-kpi-wl">
            {`W ${sum.wins ?? 0} · L ${sum.losses ?? 0} · D ${sum.draws ?? 0}`}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Plundered Resources</span>
          <strong className="kpi-value tabular" id="atk-kpi-loot">
            {`${fmtInt(Math.round(finiteNum(sum.total_loot_spice_eq, 0)))} SpEq`}
          </strong>
          <span className="kpi-sub tabular" id="atk-kpi-loss">
            {`Attacker losses: ${fmtInt(Math.round(finiteNum(sum.total_losses_spice_eq, 0)))} SpEq`}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">24h Raid &amp; Bash Status</span>
          <strong className="kpi-value tabular" id="atk-kpi-raids24">
            {`${fmtInt(raids24)} / 24h`}
          </strong>
          <span className="kpi-sub tabular" id="atk-kpi-blocks">
            {atkData
              ? `${fmtInt(sum.active_blocks_count ?? 0)} active bash timeouts`
              : "0 active bash blocks"}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label tabular">Battle Report Sync</span>
          <div className="combat-jumps">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              id="btn-atk-sync"
              onClick={syncAllInboxFolders}
            >
              Sync All Folders
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              id="btn-atk-refresh"
              onClick={refreshAttacksView}
            >
              Refresh DB
            </button>
          </div>
        </div>
      </div>

      <article className="hud-window page-fill">
        <div className="window-bar window-bar-wrap">
          <div className="window-title-group">
            <span className="module-code tabular">ATK</span>
            <h2>Combat Reports, Plunder &amp; Bash Timeouts</h2>
            <span className="module-meta tabular" id="atk-meta-count">
              {atkData
                ? `${battles.length} REPORTS · ${blocks.length} BLOCKS`
                : "0 REPORTS"}
            </span>
          </div>
          <div className="tab-group" role="tablist" aria-label="Attack view subtabs">
            <button
              type="button"
              className={`tab-btn${atkTab === "battles" ? " active" : ""}`}
              data-atk-tab="battles"
              onClick={() => setUiPref("atkTab", "battles")}
            >
              Battle Reports
            </button>
            <button
              type="button"
              className={`tab-btn${atkTab === "blocks" ? " active" : ""}`}
              data-atk-tab="blocks"
              onClick={() => setUiPref("atkTab", "blocks")}
            >
              Bash &amp; Server Blocks
            </button>
          </div>
        </div>

        <div className="intel-pane" id="atk-pane-battles" hidden={atkTab !== "battles"}>
          <div className="table-wrap table-scroll">
            <table>
              <thead>
                <tr>
                  <SortableTh
                    label="Time"
                    sortKey="time"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                  />
                  <SortableTh
                    label="Target"
                    sortKey="target"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                  />
                  <SortableTh
                    label="Defender"
                    sortKey="defender"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                  />
                  <SortableTh
                    label="Outcome"
                    sortKey="outcome"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                  />
                  <SortableTh
                    label="Rounds"
                    sortKey="rounds"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Plundered Loot"
                    sortKey="loot"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                  />
                  <SortableTh
                    label="Losses (Atk / Def)"
                    sortKey="losses"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                  />
                  <SortableTh
                    label="Debris Created"
                    sortKey="debris"
                    activeKey={battleSort.sortKey}
                    direction={battleSort.sortDirection}
                    onSort={battleSort.requestSort}
                  />
                </tr>
              </thead>
              <tbody id="atk-battles-tbody">
                {battleSort.sortedRows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="empty-cell">
                      Click Sync All Folders to archive in-game battle reports.
                    </td>
                  </tr>
                ) : (
                  battleSort.sortedRows.map((b, idx) => {
                    const outVal = String(b.outcome || "draw").toLowerCase();
                    const outCls =
                      outVal === "win" ? "is-win" : outVal === "loss" ? "is-loss" : "is-draw";
                    const lootSp = Math.round(finiteNum(b.loot?.spice_eq, 0));
                    const atkLoss = Math.round(finiteNum(b.attacker_losses?.spice_eq, 0));
                    const defLoss = Math.round(finiteNum(b.defender_losses?.spice_eq, 0));
                    const dPig = Math.round(finiteNum(b.debris?.pig_iron, 0));
                    const dMet = Math.round(finiteNum(b.debris?.metal, 0));

                    return (
                      <tr key={b.id ?? `${b.coords}-${idx}`}>
                        <td className="tabular">
                          {fmtShortDateTime(b.ts, b.msg_date_text)}
                        </td>
                        <td className="tabular">{String(b.coords || "—")}</td>
                        <td>{String(b.defender_name || b.subject || "Unknown")}</td>
                        <td>
                          <span className={`outcome-badge ${outCls}`}>
                            {outVal.toUpperCase()}
                          </span>
                        </td>
                        <td className="col-right tabular">
                          {String(Math.round(finiteNum(b.rounds, 0)))}
                        </td>
                        <td className="tabular">
                          {lootSp > 0
                            ? `${formatResourcesInline(b.loot)} (+${fmtInt(lootSp)} SpEq)`
                            : "0"}
                        </td>
                        <td className="tabular">
                          {`Atk ${fmtInt(atkLoss)} / Def ${fmtInt(defLoss)} SpEq`}
                        </td>
                        <td className="tabular">
                          {dPig > 0 || dMet > 0
                            ? `${fmtInt(dPig)} Pig · ${fmtInt(dMet)} Met`
                            : "None"}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="intel-pane" id="atk-pane-blocks" hidden={atkTab !== "blocks"}>
          <div className="table-wrap table-scroll">
            <table>
              <thead>
                <tr>
                  <SortableTh
                    label="Coords"
                    sortKey="coords"
                    activeKey={blockSort.sortKey}
                    direction={blockSort.sortDirection}
                    onSort={blockSort.requestSort}
                  />
                  <SortableTh
                    label="Owner / Planet"
                    sortKey="owner"
                    activeKey={blockSort.sortKey}
                    direction={blockSort.sortDirection}
                    onSort={blockSort.requestSort}
                  />
                  <SortableTh
                    label="Bash Hits"
                    sortKey="bashHits"
                    activeKey={blockSort.sortKey}
                    direction={blockSort.sortDirection}
                    onSort={blockSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Cooldown Left"
                    sortKey="cooldown"
                    activeKey={blockSort.sortKey}
                    direction={blockSort.sortDirection}
                    onSort={blockSort.requestSort}
                    align="right"
                  />
                  <SortableTh
                    label="Server Protection Reason"
                    sortKey="reason"
                    activeKey={blockSort.sortKey}
                    direction={blockSort.sortDirection}
                    onSort={blockSort.requestSort}
                  />
                  <th className="col-right">Ops</th>
                </tr>
              </thead>
              <tbody id="atk-blocks-tbody">
                {blockSort.sortedRows.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="empty-cell">
                      No active bash protection or server blocks recorded.
                    </td>
                  </tr>
                ) : (
                  blockSort.sortedRows.map((bt, idx) => {
                    const coords = String(bt.coords || "—");
                    return (
                      <tr key={`${coords}-${idx}`}>
                        <td className="tabular">{coords}</td>
                        <td>
                          {[bt.planet_name, bt.owner].filter(Boolean).join(" · ") || "Unknown"}
                        </td>
                        <td className="col-right tabular">
                          {String(Math.round(finiteNum(bt.bash_hits, 0)))}
                        </td>
                        <td className="col-right tabular">
                          {bt.active_block
                            ? fmtDurationCountdown(bt.remaining_seconds)
                            : "Cleared"}
                        </td>
                        <td>{String(bt.block_reason || "Bash / Server protection")}</td>
                        <td className="col-right">
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            onClick={() => handleSpyBlockTarget(coords)}
                          >
                            Spy
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </article>
    </section>
  );
};

export default AttacksView;
