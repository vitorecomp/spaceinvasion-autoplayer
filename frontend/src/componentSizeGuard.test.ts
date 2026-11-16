import { describe, expect, it } from "vitest";

const MAX_TSX_COMPONENT_LOC = 600;

async function collectProductionFiles(
  ext: ".tsx" | ".ts"
): Promise<Array<{ relPath: string; lineCount: number; content: string }>> {
  const fsMod = ["node", "fs"].join(":");
  const pathMod = ["node", "path"].join(":");
  const urlMod = ["node", "url"].join(":");
  const fs = (await import(/* @vite-ignore */ fsMod)) as any;
  const path = (await import(/* @vite-ignore */ pathMod)) as any;
  const url = (await import(/* @vite-ignore */ urlMod)) as any;

  const srcRoot = path.dirname(url.fileURLToPath(import.meta.url));
  const results: Array<{ relPath: string; lineCount: number; content: string }> = [];

  const walk = (dir: string) => {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === "__tests__" || entry.name === "node_modules") continue;
        walk(fullPath);
        continue;
      }
      if (!entry.isFile()) continue;
      if (!entry.name.endsWith(ext)) continue;
      if (entry.name.includes(".test.") || entry.name.includes(".spec.")) continue;
      const content = String(fs.readFileSync(fullPath, "utf-8"));
      const normalized = content.endsWith("\n") ? content.slice(0, -1) : content;
      const lineCount = normalized.length === 0 ? 0 : normalized.split(/\r?\n/).length;
      const relPath = path.relative(srcRoot, fullPath).split(path.sep).join("/");
      results.push({ relPath, lineCount, content });
    }
  };

  walk(srcRoot);
  results.sort((a, b) => a.relPath.localeCompare(b.relPath));
  return results;
}

describe("Phase 5.3 component modularity & SVG invariants", () => {
  it(`enforces <= ${MAX_TSX_COMPONENT_LOC} LOC across all production .tsx components`, async () => {
    const tsxFiles = await collectProductionFiles(".tsx");
    expect(tsxFiles.length).toBeGreaterThan(20);

    const violations = tsxFiles
      .filter((f) => f.lineCount > MAX_TSX_COMPONENT_LOC)
      .map((f) => `${f.relPath} (${f.lineCount} LOC)`);

    expect(violations).toEqual([]);
  });

  it("preserves the strict chart/graph-only inline SVG allowlist (no UI icon SVGs)", async () => {
    const tsxFiles = await collectProductionFiles(".tsx");
    const svgFiles: string[] = [];

    for (const file of tsxFiles) {
      if (/<svg\b/i.test(file.content)) {
        svgFiles.push(file.relPath);
      }
    }

    expect(svgFiles.sort()).toEqual([
      "components/layout/ScannerHudRadial.tsx",
      "features/action-graph/ConfigOptimizerYearlyGraphModal.tsx",
      "features/action-graph/DagCanvas.tsx",
      "features/ranking/RankTrajectorySvg.tsx",
    ]);
  });

  it("forbids dead legacy (window as any).state writes in AppHeader (C20)", async () => {
    const tsxFiles = await collectProductionFiles(".tsx");
    const appHeader = tsxFiles.find((f) => f.relPath === "components/layout/AppHeader.tsx");
    expect(appHeader).toBeDefined();
    expect(appHeader!.content).not.toMatch(/window\s+as\s+any\)\s*\.state\b/);
    expect(appHeader!.content).not.toMatch(/\bwindow\.state\b/);
  });
});
