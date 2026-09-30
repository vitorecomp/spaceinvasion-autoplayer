import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8787";

export default defineConfig({
  plugins: [react()],
  publicDir: false,
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    watch: {
      usePolling: true,
      interval: 400,
    },
    proxy: {
      "/health": { target: apiProxyTarget, changeOrigin: true },
      "/bot_status": { target: apiProxyTarget, changeOrigin: true },
      "/ui-version": { target: apiProxyTarget, changeOrigin: true },
      "/state": { target: apiProxyTarget, changeOrigin: true },
      "/dashboard": { target: apiProxyTarget, changeOrigin: true },
      "/command": { target: apiProxyTarget, changeOrigin: true },
      "/bell.svg": { target: apiProxyTarget, changeOrigin: true },
      "/favicon.svg": { target: apiProxyTarget, changeOrigin: true },
      "/favicon.ico": { target: apiProxyTarget, changeOrigin: true },
    },
  },
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
