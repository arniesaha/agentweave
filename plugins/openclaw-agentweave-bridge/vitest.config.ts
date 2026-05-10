import { defineConfig } from "vitest/config"
import path from "node:path"

// The plugin imports `openclaw/plugin-sdk/diagnostic-runtime` at runtime from
// the host OpenClaw process, which is not present in the plugin's local
// node_modules. Alias it to a no-op stub so vitest can load service.ts.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
  resolve: {
    alias: {
      "openclaw/plugin-sdk/diagnostic-runtime": path.resolve(
        __dirname,
        "test/stubs/openclaw-plugin-sdk-diagnostic-runtime.ts",
      ),
    },
  },
})
