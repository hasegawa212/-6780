export interface FollowUpItem {
  readonly id: string;
  readonly dueAt: Date;
  readonly completed: boolean;
}

export interface FollowUpBuckets<T extends FollowUpItem> {
  readonly overdue: T[];
  readonly today: T[];
  readonly upcoming: T[];
  readonly completed: T[];
}

function localDateKey(instant: Date, timeZone: string): string {
  // 不正なタイムゾーンは Intl が RangeError を投げる。黙って UTC にはしない
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(instant);
}

const byDue = (a: FollowUpItem, b: FollowUpItem) => a.dueAt.getTime() - b.dueAt.getTime();

/** フォローアップを 期限切れ / 今日 / 今後 / 完了 に分ける。「今日」は組織の現地日付で判定する。 */
export function bucketFollowUps<T extends FollowUpItem>(
  items: readonly T[],
  now: Date,
  timeZone: string,
): FollowUpBuckets<T> {
  const today = localDateKey(now, timeZone);
  const out: FollowUpBuckets<T> = { overdue: [], today: [], upcoming: [], completed: [] };
  for (const item of items) {
    if (item.completed) out.completed.push(item);
    else if (item.dueAt.getTime() < now.getTime()) out.overdue.push(item);
    else if (localDateKey(item.dueAt, timeZone) === today) out.today.push(item);
    else out.upcoming.push(item);
  }
  out.overdue.sort(byDue);
  out.today.sort(byDue);
  out.upcoming.sort(byDue);
  out.completed.sort((a, b) => byDue(b, a));
  return out;
}
