import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToString } from "react-dom/server";
import { createServer } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, "..");
const outDir = path.resolve(frontendRoot, "../spaceinvasion/ui");

async function prerender() {
  const vite = await createServer({
    root: frontendRoot,
    logLevel: "error",
    server: { middlewareMode: true },
    appType: "custom",
  });

  try {
    const mod = await vite.ssrLoadModule("/src/App.tsx");
    const App = mod.App || mod.default;
    const ssrHtml = renderToString(React.createElement(App, { disableInitialFetch: true }));

    const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>SpaceInvasion Control — Tactical Operations Console</title>
  <meta name="description" content="Tactical single-screen operations console for SpaceInvasion Classic autoplayer: dashboard, building reach ETAs, Blast furnace Pig iron bucket, 1:1 combat simulator, ranking evolution, and live telemetry." />
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <link rel="icon" type="image/x-icon" href="/favicon.ico" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700&family=Chakra+Petch:wght@500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet" />
  <link id="ui-stylesheet" rel="stylesheet" href="/styles.css" />
</head>
<body>
  <div id="root">${ssrHtml}</div>
  <script src="/app.js"></script>
</body>
</html>
`;

    // Verify strict HTML invariants before writing
    const attrPattern = /="([^"]*)"/g;
    let match;
    while ((match = attrPattern.exec(html)) !== null) {
      if (match[1].includes(">")) {
        throw new Error(`Forbidden literal '>' inside HTML attribute value: ${match[0]}`);
      }
    }

    const svgMatches = [...html.matchAll(/<svg\b[^>]*>/gi)].map((m) => m[0]);
    const nonChartSvgs = svgMatches.filter((tag) => !tag.includes('id="rnk-chart"'));
    if (nonChartSvgs.length !== 1 || !nonChartSvgs[0].includes('id="gann-radial-svg"')) {
      throw new Error(
        `Expected exactly 1 non-chart <svg> (#gann-radial-svg), found ${nonChartSvgs.length}: ${nonChartSvgs.join(", ")}`
      );
    }

    const outPath = path.join(outDir, "index.html");
    fs.writeFileSync(outPath, html, "utf-8");
    console.log(`Prerendered ${outPath} (${html.length} bytes, ${svgMatches.length} SVGs).`);
  } finally {
    await vite.close();
  }
}

prerender().catch((err) => {
  console.error(err);
  process.exit(1);
});
