import { NodeSDK } from '@opentelemetry/sdk-node';

export class AgentWeaveConfig {
  static agentId: string;
  static agentModel?: string;
  static agentVersion?: string;
  static otlpEndpoint: string;
  static capturesInput?: boolean;
  static capturesOutput?: boolean;
  static enabled: boolean = false;

  static setup(config: {
    agentId: string;
    agentModel?: string;
    agentVersion?: string;
    otlpEndpoint: string;
    capturesInput?: boolean;
    capturesOutput?: boolean;
  }) {
    this.agentId = config.agentId;
    this.agentModel = config.agentModel;
    this.agentVersion = config.agentVersion;
    this.otlpEndpoint = config.otlpEndpoint;
    this.capturesInput = config.capturesInput;
    this.capturesOutput = config.capturesOutput;
    this.enabled = true;

    const sdk = new NodeSDK({
      traceExporter: new (require('@opentelemetry/exporter-trace-otlp-http').OTLPTraceExporter)({
        url: this.otlpEndpoint
      }),
    });
    sdk.start();
  }
}