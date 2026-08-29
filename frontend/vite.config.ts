import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: path.resolve(__dirname, "../spaceinvasion/ui"),
    emptyOutDir: false,
    cssCodeSplit: false,
    minify: false,
    sourcemap: false,
    rollupOptions: {
      input: path.resolve(__dirname, "src/main.tsx"),
      output: {
        format: "iife",
        name: "SpaceInvasionConsoleApp",
        entryFileNames: "app.js",
        assetFileNames: (assetInfo) => {
          const name = assetInfo.name || "";
          if (name.endsWith(".css")) {
            return "styles.css";
          }
          return "assets/[name][extname]";
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
