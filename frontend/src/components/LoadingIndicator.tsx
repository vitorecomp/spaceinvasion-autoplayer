import React from "react";

export interface LoadingIndicatorProps {
  id?: string;
  message: string;
  detail?: string;
  variant?: "inline" | "panel";
  className?: string;
}

export const LoadingIndicator: React.FC<LoadingIndicatorProps> = ({
  id,
  message,
  detail,
  variant = "inline",
  className = "",
}) => {
  const classes = [
    "intel-loading",
    variant === "panel" ? "intel-loading-panel" : "intel-loading-inline",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      id={id}
      className={classes}
      role="status"
      aria-live="polite"
      aria-busy="true"
      data-loading-variant={variant}
    >
      <div className="intel-loading-row">
        <span className="intel-loading-spinner" aria-hidden="true" />
        <div className="intel-loading-copy">
          <span className="intel-loading-message tabular">
            <span>{message}</span>
            <span className="intel-loading-dots" aria-hidden="true">
              <span className="intel-loading-dot" />
              <span className="intel-loading-dot" />
              <span className="intel-loading-dot" />
            </span>
          </span>
          {detail ? <span className="intel-loading-detail">{detail}</span> : null}
        </div>
      </div>
      <div className="intel-loading-track" aria-hidden="true">
        <span className="intel-loading-sweep" />
      </div>
    </div>
  );
};

export default LoadingIndicator;
