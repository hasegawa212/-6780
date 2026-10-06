import type { SafetyControls } from "./ports.js";

/**
 * 設定（OUTBOUND_CALLS_ENABLED）による発信ゲートを、組織の全発信停止スイッチに重ねる。
 * api / worker はこれで包んだ SafetyControls を CreateCall に渡す。
 * - 設定で OFF → 常に停止
 * - ON → 組織のスイッチに従う。照会に失敗したら停止（fail closed）
 */
export function withDeploymentGate(
  outboundCallsEnabled: boolean,
  inner: SafetyControls,
): SafetyControls {
  return {
    async isOutboundStopped() {
      if (!outboundCallsEnabled) return true;
      try {
        return (await inner.isOutboundStopped()) !== false;
      } catch {
        return true;
      }
    },
  };
}
