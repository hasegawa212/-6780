import type {
  CreateProviderCallRequest,
  ProviderCall,
  TelephonyProvider,
  TransferTarget,
} from "@tac/application";
import type { CallStatus } from "@tac/domain";

/** 本物の電話はかけない、テスト・ローカル・デモ用のプロバイダ（ADR-0004）。 */
export class MockTelephonyProvider implements TelephonyProvider {
  readonly name = "mock";
  readonly placed: CreateProviderCallRequest[] = [];
  readonly transfers: { providerCallId: string; to: string }[] = [];
  private readonly byKey = new Map<string, string>();
  private readonly statuses = new Map<string, CallStatus>();

  async createCall(request: CreateProviderCallRequest): Promise<ProviderCall> {
    const known = this.byKey.get(request.idempotencyKey);
    if (known) return this.view(known);
    this.placed.push(request);
    const id = `MOCK-${this.placed.length}`;
    this.byKey.set(request.idempotencyKey, id);
    this.statuses.set(id, "DIALING");
    return this.view(id);
  }

  async endCall(providerCallId: string): Promise<void> {
    this.setStatus(providerCallId, "ENDED");
  }

  async transferCall(providerCallId: string, target: TransferTarget): Promise<void> {
    this.require(providerCallId);
    this.transfers.push({ providerCallId, to: target.to });
  }

  async getCall(providerCallId: string): Promise<ProviderCall> {
    return this.view(providerCallId);
  }

  /** テストから回線の状態変化（Webhook 相当）を起こす */
  setStatus(providerCallId: string, status: CallStatus): void {
    this.require(providerCallId);
    this.statuses.set(providerCallId, status);
  }

  private require(providerCallId: string): void {
    if (!this.statuses.has(providerCallId)) throw new Error(`unknown call ${providerCallId}`);
  }

  private view(providerCallId: string): ProviderCall {
    this.require(providerCallId);
    return {
      provider: this.name,
      providerCallId,
      status: this.statuses.get(providerCallId) ?? "FAILED",
    };
  }
}

export type AppEnv = "local" | "test" | "staging" | "production";
export type ProviderName = "mock" | "twilio" | "openai-sip";

export class ProviderNotAllowedError extends Error {
  constructor(provider: ProviderName, appEnv: AppEnv) {
    super(`telephony provider "${provider}" is not allowed in "${appEnv}" (only "mock")`);
    this.name = "ProviderNotAllowedError";
  }
}

/** local / test では本物の回線につながるアダプタを作れないようにする（テストから実際に発信しない保証）。 */
export function createTelephonyProvider(config: {
  appEnv: AppEnv;
  provider: ProviderName;
}): TelephonyProvider {
  if (config.provider === "mock") return new MockTelephonyProvider();
  if (config.appEnv === "local" || config.appEnv === "test") {
    throw new ProviderNotAllowedError(config.provider, config.appEnv);
  }
  throw new Error(`telephony provider "${config.provider}" is not implemented yet (Phase 10/11)`);
}
