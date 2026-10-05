/** 発信してよい時間帯。相手のタイムゾーン（IANA 名）で判定する。 */
export interface CallingWindowPolicy {
  readonly timeZone: string;
  /** 0:00 からの分。[startMinute, endMinute) の半開区間 */
  readonly startMinute: number;
  readonly endMinute: number;
  /** 発信してよい曜日（0=日 … 6=土） */
  readonly weekdays: readonly number[];
  /** 発信しない日（相手の現地日付、YYYY-MM-DD） */
  readonly holidays: readonly string[];
}

interface LocalParts {
  readonly year: number;
  readonly month: number;
  readonly day: number;
  readonly minuteOfDay: number;
  readonly weekday: number;
}

const WEEKDAY: Readonly<Record<string, number>> = {
  Sun: 0,
  Mon: 1,
  Tue: 2,
  Wed: 3,
  Thu: 4,
  Fri: 5,
  Sat: 6,
};
const DAY_MS = 24 * 60 * 60 * 1000;

const formatters = new Map<string, Intl.DateTimeFormat>();
function formatter(timeZone: string): Intl.DateTimeFormat {
  let f = formatters.get(timeZone);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      weekday: "short",
      hourCycle: "h23",
    });
    formatters.set(timeZone, f);
  }
  return f;
}

function localParts(instant: Date, timeZone: string): LocalParts {
  const parts: Record<string, string> = {};
  for (const p of formatter(timeZone).formatToParts(instant)) parts[p.type] = p.value;
  const num = (k: string) => Number(parts[k]);
  return {
    year: num("year"),
    month: num("month"),
    day: num("day"),
    minuteOfDay: num("hour") * 60 + num("minute"),
    weekday: WEEKDAY[parts.weekday ?? ""] ?? -1,
  };
}

const isoDate = (y: number, m: number, d: number) =>
  `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;

function isCallableDay(p: CallingWindowPolicy, y: number, m: number, d: number, weekday: number) {
  return p.weekdays.includes(weekday) && !p.holidays.includes(isoDate(y, m, d));
}

export function isWithinCallingWindow(instant: Date, policy: CallingWindowPolicy): boolean {
  let l: LocalParts;
  try {
    l = localParts(instant, policy.timeZone);
  } catch {
    // 不正なタイムゾーン等で現地時刻が分からないときは「時間外」として扱う（fail closed, QA-NX-05）
    return false;
  }
  return (
    isCallableDay(policy, l.year, l.month, l.day, l.weekday) &&
    l.minuteOfDay >= policy.startMinute &&
    l.minuteOfDay < policy.endMinute
  );
}

/** 現地の日付・時刻を UTC の瞬間に変換する（夏時間の境目も2回補正して合わせる）。 */
function zonedToUtc(y: number, m: number, d: number, minuteOfDay: number, timeZone: string): Date {
  const wall = Date.UTC(y, m - 1, d, Math.floor(minuteOfDay / 60), minuteOfDay % 60);
  const offsetAt = (t: number) => {
    const l = localParts(new Date(t), timeZone);
    const asUtc = Date.UTC(l.year, l.month - 1, l.day, 0, l.minuteOfDay);
    return asUtc - Math.floor(t / 60000) * 60000;
  };
  let guess = wall - offsetAt(wall);
  const corrected = wall - offsetAt(guess);
  if (corrected !== guess) guess = corrected;
  return new Date(guess);
}

/** instant 以降で最初に発信できる瞬間。すでに時間内なら instant をそのまま返す。 */
export function nextWindowStart(instant: Date, policy: CallingWindowPolicy): Date {
  if (isWithinCallingWindow(instant, policy)) return instant;
  const today = localParts(instant, policy.timeZone);
  // 祝日が連続しても1年先までには必ず見つかる前提（見つからなければ設定の誤り）
  for (let i = 0; i <= 366; i++) {
    const cal = new Date(Date.UTC(today.year, today.month - 1, today.day) + i * DAY_MS);
    const y = cal.getUTCFullYear();
    const m = cal.getUTCMonth() + 1;
    const d = cal.getUTCDate();
    if (!isCallableDay(policy, y, m, d, cal.getUTCDay())) continue;
    const start = zonedToUtc(y, m, d, policy.startMinute, policy.timeZone);
    if (start.getTime() >= instant.getTime()) return start;
  }
  throw new RangeError("calling window policy allows no day within a year");
}
