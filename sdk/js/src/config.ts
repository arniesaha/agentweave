import { NodeSDK } from '@opentelemetry/sdk-node';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { shutdownHandler } from './tracer';

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
      traceExporter: new OTLPTraceExporter({
        url: this.otlpEndpoint,
      }),
    });
    sdk.start();
  }

  /**
   * Manually close all open spans and flush the OTel exporter.
   *
   * Equivalent to the automatic SIGTERM/SIGINT handlers but callable directly
   * from application code for controlled teardowns.
   *
   * @example
   * process.on('beforeExit', () => AgentWeaveConfig.shutdown());
   */
  static shutdown(): void {
    shutdownHandler('manual');
  }
}
