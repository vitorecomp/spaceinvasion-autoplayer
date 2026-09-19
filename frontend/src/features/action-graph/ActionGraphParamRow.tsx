import React from "react";
import type { ActionGraphParamDoc } from "./paramDocs";

export type ActionGraphParamLayout = "inline" | "stack" | "toggle";

export interface ActionGraphParamRowProps {
  doc: ActionGraphParamDoc;
  htmlFor: string;
  layout?: ActionGraphParamLayout;
  titleOverride?: string;
  children: React.ReactNode;
}

export const ActionGraphParamRow: React.FC<ActionGraphParamRowProps> = ({
  doc,
  htmlFor,
  layout = "inline",
  titleOverride,
  children,
}) => {
  const titleText = titleOverride || doc.title;

  if (layout === "stack") {
    return (
      <div className="ag-param-item is-stack">
        <div className="ag-param-item-head">
          <label className="ag-param-item-label" htmlFor={htmlFor}>
            {titleText}
          </label>
        </div>
        <div className="ag-param-item-meta">
          <code className="ag-param-item-key tabular">{doc.paramKey}</code>
          <span className="ag-param-item-bounds tabular">{doc.boundsLabel}</span>
        </div>
        <div className="ag-param-item-control is-full">{children}</div>
        <p className="ag-param-item-desc">{doc.description}</p>
      </div>
    );
  }

  return (
    <div className={`ag-param-item${layout === "toggle" ? " is-toggle" : " is-inline"}`}>
      <div className="ag-param-item-top">
        <label className="ag-param-item-label" htmlFor={htmlFor}>
          {titleText}
        </label>
        <div className={`ag-param-item-control${layout === "toggle" ? " is-toggle" : " is-compact"}`}>
          {children}
        </div>
      </div>
      <div className="ag-param-item-meta">
        <code className="ag-param-item-key tabular">{doc.paramKey}</code>
        <span className="ag-param-item-bounds tabular">{doc.boundsLabel}</span>
      </div>
      <p className="ag-param-item-desc">{doc.description}</p>
    </div>
  );
};

export default ActionGraphParamRow;
