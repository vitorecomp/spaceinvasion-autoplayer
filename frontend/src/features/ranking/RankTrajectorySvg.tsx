import React from "react";
import { finiteNum } from "../../utils/formatters";

export interface RankTrajectorySvgProps {
  samples?: Array<Record<string, any>> | null;
}

export const RankTrajectorySvg: React.FC<RankTrajectorySvgProps> = ({ samples }) => {
  const w = 420;
  const h = 130;
  const padX = 14;
  const padY = 14;

  const vals = (Array.isArray(samples) ? samples : [])
    .map((s) => finiteNum(s?.estimated_points, null))
    .filter((v): v is number => v !== null);

  let minV = 0;
  let maxV = 0;
  let rawSpan = 0;
  let span = 0.5;

  if (vals.length > 0) {
    minV = Math.min(...vals);
    maxV = Math.max(...vals);
    rawSpan = maxV - minV;
    span = Number.isFinite(rawSpan) && rawSpan >= 0.5 ? rawSpan : 0.5;
  }

  const toXY = (idx: number, val: number): [string, string] => {
    const x = vals.length === 1 ? w / 2 : padX + (idx / (vals.length - 1)) * (w - padX * 2);
    const ratio = Number.isFinite(rawSpan) && rawSpan > 0 ? (val - minV) / span : 0;
    const rawY = h - padY - (Number.isFinite(ratio) ? ratio : 0) * (h - padY * 2);
    const y = Number.isFinite(rawY) ? rawY : h / 2;
    return [x.toFixed(1), y.toFixed(1)];
  };

  const ptsAttr = vals.length >= 2 ? vals.map((v, i) => toXY(i, v).join(",")).join(" ") : "";

  return (
    <svg
      id="rnk-chart"
      viewBox="0 0 420 130"
      role="img"
      aria-label="Estimated empire points over recent observations"
    >
      {[1, 2, 3].map((i) => {
        const y = Math.round((h / 4) * i);
        return (
          <line
            key={i}
            x1={String(padX)}
            x2={String(w - padX)}
            y1={String(y)}
            y2={String(y)}
            stroke="#243135"
            strokeDasharray="3 3"
          />
        );
      })}
      {vals.length === 0 ? (
        <text x={String(w / 2)} y={String(h / 2)} textAnchor="middle" fill="#9eb3b8" fontSize="12">
          Collecting samples into SQLite (rank_snapshots)...
        </text>
      ) : (
        <>
          {vals.length >= 2 && (
            <polyline fill="none" stroke="#16ec98" strokeWidth="2" points={ptsAttr} />
          )}
          {vals.map((v, i) => {
            const [cx, cy] = toXY(i, v);
            const isLast = i === vals.length - 1;
            return (
              <circle
                key={i}
                cx={cx}
                cy={cy}
                r={isLast ? "3.5" : "2"}
                fill={isLast ? "#e6eef0" : "#16ec98"}
              />
            );
          })}
        </>
      )}
    </svg>
  );
};

export default RankTrajectorySvg;
