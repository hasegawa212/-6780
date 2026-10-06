import type { E164 } from "@tac/domain";
import type { OrganizationId, SuppressionService } from "./ports.js";

export interface ContactabilityCheck {
  /** true のときだけ発信・キュー投入してよい */
  readonly allowed: boolean;
  /** 照会そのものが失敗した（監査に残すため区別する） */
  readonly unavailable: boolean;
}

/**
 * 抑止（DNC）の照会は fail closed。照会が失敗したとき・true 以外の値が返ったときは、
 * すべて「抑止中」として扱う。判定できないことを「発信してよい」に読み替えない。
 * 発信（CreateCall）と候補一覧（CallQueue）は必ずこの関数を通す。
 */
export async function isContactable(
  suppression: Pick<SuppressionService, "canContact">,
  organizationId: OrganizationId,
  phone: E164,
): Promise<ContactabilityCheck> {
  try {
    const answer = await suppression.canContact(organizationId, phone);
    return { allowed: answer === true, unavailable: false };
  } catch {
    return { allowed: false, unavailable: true };
  }
}
