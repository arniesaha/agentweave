declare module "openclaw/plugin-sdk/diagnostics-otel" {
  export type OpenClawPluginApi = {
    registerService(service: unknown): void
  }
}

declare module "openclaw/plugin-sdk/diagnostic-runtime" {
  export function onDiagnosticEvent(listener: (evt: unknown) => void): () => void
  export function onModelDiagnosticEvent(listener: (evt: unknown) => void): () => void
  export function onTrustedDiagnosticEvent(
    listener: (evt: unknown, privateData: unknown) => void,
  ): () => void
}
