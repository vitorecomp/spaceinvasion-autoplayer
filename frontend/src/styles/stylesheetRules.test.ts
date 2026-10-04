import { beforeAll, describe, expect, it } from "vitest";

interface NodeFsSubset {
  readFileSync: (filePath: string, encoding: "utf8") => string;
}

interface NodePathSubset {
  dirname: (filePath: string) => string;
  resolve: (...segments: string[]) => string;
}

interface NodeUrlSubset {
  fileURLToPath: (fileUrl: string) => string;
}

function stripCssComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

function extractMediaBlocks(css: string, queryPattern: RegExp): string[] {
  const blocks: string[] = [];
  const flags = queryPattern.flags.includes("g") ? queryPattern.flags : `${queryPattern.flags}g`;
  const matcher = new RegExp(queryPattern.source, flags);
  let match: RegExpExecArray | null;
  while ((match = matcher.exec(css)) !== null) {
    const openBraceIdx = match.index + match[0].length - 1;
    let depth = 0;
    let endIdx = -1;
    for (let i = openBraceIdx; i < css.length; i += 1) {
      if (css[i] === "{") depth += 1;
      else if (css[i] === "}") {
        depth -= 1;
        if (depth === 0) {
          endIdx = i;
          break;
        }
      }
    }
    if (endIdx !== -1) {
      blocks.push(css.slice(openBraceIdx + 1, endIdx));
    }
  }
  return blocks;
}

describe("Stylesheet invariants (responsive floors, typography & 3-column dashboard layout)", () => {
  let combinedCss = "";
  let cleanDashboardCss = "";
  let mobile1100Blocks: string[] = [];
  let mobile1100Css = "";

  beforeAll(async () => {
    const fs = (await import(/* @vite-ignore */ ["node", "fs"].join(":"))) as NodeFsSubset;
    const pathMod = (await import(/* @vite-ignore */ ["node", "path"].join(":"))) as NodePathSubset;
    const urlMod = (await import(/* @vite-ignore */ ["node", "url"].join(":"))) as NodeUrlSubset;

    const stylesDir = import.meta.url.startsWith("file:")
      ? pathMod.dirname(urlMod.fileURLToPath(import.meta.url))
      : pathMod.resolve("src", "styles");

    const indexCss = stripCssComments(
      fs.readFileSync(pathMod.resolve(stylesDir, "index.css"), "utf8"),
    );
    const imports = Array.from(
      indexCss.matchAll(/@import\s+["']([^"']+)["']\s*;/g),
      (match) => match[1],
    );
    expect(imports.length).toBeGreaterThan(0);

    const moduleContents = imports.map((specifier) =>
      stripCssComments(fs.readFileSync(pathMod.resolve(stylesDir, specifier), "utf8")),
    );
    combinedCss = moduleContents.join("\n");
    cleanDashboardCss = stripCssComments(
      fs.readFileSync(pathMod.resolve(stylesDir, "dashboard.css"), "utf8"),
    );
    mobile1100Blocks = extractMediaBlocks(
      combinedCss,
      /@media\s*\(\s*max-width\s*:\s*1100px\s*\)\s*\{/g,
    );
    mobile1100Css = mobile1100Blocks.join("\n");
  });

  it("enforces readable 0.86rem typography floors for .nav-title and .eta-pill", () => {
    expect(combinedCss).toMatch(/\.nav-title\s*\{[^}]*font-size\s*:\s*0\.86rem/);
    expect(combinedCss).toMatch(/\.eta-pill\s*\{[^}]*font-size\s*:\s*0\.86rem/);
  });

  it("releases 100vh viewport lock and preserves scroll/min-height floors in @media (max-width: 1100px)", () => {
    expect(mobile1100Blocks.length).toBeGreaterThan(0);
    expect(mobile1100Css).toContain(".table-scroll");
    expect(mobile1100Css).toContain(".page-fill");
    expect(mobile1100Css).toContain(".dash-lower-grid");
    expect(mobile1100Css).toContain(".split-workspace");
    expect(mobile1100Css).toMatch(/flex\s*:\s*0\s+0\s+auto/);
    expect(mobile1100Css).toMatch(/\.table-scroll\s*\{[^}]*overflow-x\s*:\s*auto/);
    expect(mobile1100Css).toMatch(/\.table-scroll\s*\{[^}]*overflow-y\s*:\s*visible/);

    const logsMatch = mobile1100Css.match(/#logs-pre\s*\{[^}]*min-height\s*:\s*(\d+)px/);
    expect(logsMatch).not.toBeNull();
    expect(Number(logsMatch![1])).toBeGreaterThanOrEqual(200);
  });

  it("keeps desktop .dash-lower-grid as a 3-column 1fr grid without desktop max-height capping queue scroll", () => {
    expect(cleanDashboardCss).toMatch(
      /\.dash-lower-grid\s*\{[^}]*grid-template-columns\s*:\s*repeat\(\s*3\s*,\s*minmax\(\s*0\s*,\s*1fr\s*\)\s*\)/,
    );
    expect(cleanDashboardCss).toMatch(
      /\.dash-lower-grid\s*\{[^}]*grid-template-rows\s*:\s*minmax\(\s*0\s*,\s*1fr\s*\)/,
    );

    const queueScrollMatch = combinedCss.match(/\.dash-queue-card\s+\.table-scroll\s*\{([^}]*)\}/);
    expect(queueScrollMatch).not.toBeNull();
    expect(queueScrollMatch![1]).not.toContain("max-height");
  });
});
