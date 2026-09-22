import React from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import "./styles/index.css";
import "./utils/formatters";
import { App } from "./App";

function mountConsoleApp(): void {
  if (typeof document === "undefined") return;
  let rootEl = document.getElementById("root");
  if (!rootEl) {
    rootEl = document.createElement("div");
    rootEl.id = "root";
    document.body.replaceChildren(rootEl);
  } else {
    rootEl.replaceChildren();
  }
  const root = createRoot(rootEl);
  try {
    flushSync(() => {
      root.render(<App />);
    });
  } finally {
    document.documentElement.classList.remove("si-hydrating");
  }
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountConsoleApp, { once: true });
  } else {
    mountConsoleApp();
  }
}
