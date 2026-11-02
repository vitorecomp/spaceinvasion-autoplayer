import React from "react";

export const ScannerHudRadial: React.FC = () => (
  <div className="gann-scanner-stage" aria-hidden="true">
    <svg
      id="gann-radial-svg"
      className="gann-radial-svg"
      viewBox="0 0 220 160"
      width="180"
      height="130"
      focusable="false"
    >
      <path
        className="gann-axis-rail"
        d="M 110 14 L 110 146"
        fill="none"
        stroke="#244249"
        strokeWidth="1"
        strokeDasharray="3 3"
      />
      <g transform="translate(110, 120) scale(1, 0.52)">
        <circle cx="0" cy="0" r="80" fill="none" stroke="#18343b" strokeWidth="1" />
        <g className="gann-ring-mid">
          <circle
            cx="0"
            cy="0"
            r="66"
            fill="none"
            stroke="#16ec98"
            strokeOpacity="0.48"
            strokeWidth="2"
            strokeDasharray="42 16 18 14"
          />
          <circle
            cx="0"
            cy="0"
            r="46"
            fill="none"
            stroke="#32e8ff"
            strokeOpacity="0.38"
            strokeWidth="1.5"
            strokeDasharray="20 12"
          />
        </g>
      </g>
      <g transform="translate(110, 80) scale(1, 0.52)">
        <circle cx="0" cy="0" r="84" fill="none" stroke="#243135" strokeWidth="1" />
        <g className="gann-ring-gold">
          <circle
            cx="0"
            cy="0"
            r="52"
            fill="none"
            stroke="#efb03b"
            strokeWidth="2.4"
            strokeDasharray="22 8 10 8 34 10"
          />
          <circle cx="52" cy="0" r="3" fill="#efb03b" />
          <circle cx="-52" cy="0" r="3" fill="#efb03b" />
          <circle cx="0" cy="52" r="2.5" fill="#ffcf5c" />
          <circle cx="0" cy="-52" r="2.5" fill="#ffcf5c" />
        </g>
        <g className="gann-core-pulse">
          <circle cx="0" cy="0" r="26" fill="#091518" stroke="#16ec98" strokeWidth="1.8" />
          <circle
            cx="0"
            cy="0"
            r="14"
            fill="none"
            stroke="#ffcf5c"
            strokeOpacity="0.7"
            strokeWidth="1.2"
          />
          <circle cx="0" cy="0" r="7" fill="#efb03b" />
        </g>
      </g>
      <g transform="translate(110, 42) scale(1, 0.52)">
        <circle
          cx="0"
          cy="0"
          r="84"
          fill="none"
          stroke="#2f6670"
          strokeWidth="4"
          strokeDasharray="1.5 6.0"
        />
        <g className="gann-ring-outer">
          <circle
            cx="0"
            cy="0"
            r="72"
            fill="none"
            stroke="#32e8ff"
            strokeWidth="2"
            strokeDasharray="58 14 26 12 44 18"
          />
          <circle cx="0" cy="-72" r="3" fill="#32e8ff" />
          <circle cx="0" cy="72" r="3" fill="#16ec98" />
        </g>
        <g className="gann-scan-sweep">
          <path
            d="M 0 -60 A 60 60 0 0 1 60 0"
            fill="none"
            stroke="#16ec98"
            strokeWidth="3"
            strokeLinecap="round"
          />
          <path
            d="M 0 -46 L 0 -22 M 0 22 L 0 46 M -46 0 L -22 0 M 22 0 L 46 0"
            fill="none"
            stroke="#32e8ff"
            strokeOpacity="0.72"
            strokeWidth="1.2"
          />
        </g>
      </g>
    </svg>
  </div>
);
