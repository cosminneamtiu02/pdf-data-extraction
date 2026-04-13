import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": resolve(__dirname, "./src"),
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test-setup.ts"],
    css: false,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.test.{ts,tsx}",
        "src/**/*.stories.{ts,tsx}",
        "src/test-setup.ts",
        "src/test-utils.tsx",
        "src/main.tsx",
        "src/routeTree.gen.ts",
        "src/app/providers.tsx",
        "src/app/error-boundary.tsx",
        "src/routes/**",
        "src/shared/types/**",
        "src/shared/hooks/**",
        "src/shared/lib/logger.ts",
        "src/features/*/api/**",
      ],
      thresholds: {
        lines: 80,
      },
    },
  },
});
