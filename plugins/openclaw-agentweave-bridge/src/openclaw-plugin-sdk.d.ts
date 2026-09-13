declare module "openclaw/plugin-sdk/diagnostics-otel" {
  export type OpenClawPluginApi = {
    registerService(service: unknown): void
  }
}
