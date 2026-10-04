import type { FollowUpKind } from "@tac/domain";
import type { Deps } from "./deps.js";
import type { OrganizationId } from "./ports.js";

export interface QueueItem {
  readonly followUpId: string;
  readonly contactId: string;
  readonly kind: FollowUpKind;
  readonly dueAt: Date;
}

/**
 * 「次に掛ける候補」の一覧。期日の来たフォローアップを古い順に返す。
 * 抑止の登録時に予定は取り消すが、ここでも抑止を確認し直す（取り消し漏れがあっても掛けない）。
 * 候補を出すだけで、発信はしない（ADR-0003）。
 */
export class CallQueueQuery {
  constructor(
    private readonly deps: Pick<Deps, "clock" | "followUps" | "contacts" | "suppression">,
  ) {}

  async listDue(organizationId: OrganizationId): Promise<readonly QueueItem[]> {
    const { deps } = this;
    const due = await deps.followUps.listOpenDue(organizationId, deps.clock.now());
    const items: QueueItem[] = [];
    for (const f of due) {
      const contact = await deps.contacts.get(organizationId, f.contactId);
      if (!contact) continue;
      if (!(await deps.suppression.canContact(organizationId, contact.phone))) continue;
      items.push({ followUpId: f.id, contactId: f.contactId, kind: f.kind, dueAt: f.dueAt });
    }
    return items;
  }
}
