/** User-facing schedule input and output are always Shanghai time. */
export const DEFAULT_SCHEDULE_TIME_ZONE = "Asia/Shanghai";

type ZonedParts = {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
};

const formatterCache = new Map<string, Intl.DateTimeFormat>();

function getFormatter(timeZone: string) {
  const existing = formatterCache.get(timeZone);
  if (existing) return existing;
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  formatterCache.set(timeZone, formatter);
  return formatter;
}

export function getScheduleTimeParts(value: Date | string | number, timeZone = DEFAULT_SCHEDULE_TIME_ZONE): ZonedParts {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error("Invalid scheduled time");
  const values = Object.fromEntries(
    getFormatter(timeZone)
      .formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, Number(part.value)]),
  ) as Record<string, number>;
  return {
    year: values.year,
    month: values.month,
    day: values.day,
    hour: values.hour,
    minute: values.minute,
    second: values.second,
  };
}

function utcFromParts(parts: ZonedParts) {
  const value = new Date(0);
  value.setUTCFullYear(parts.year, parts.month - 1, parts.day);
  value.setUTCHours(parts.hour, parts.minute, parts.second, 0);
  return value.getTime();
}

function daysInMonth(year: number, month: number) {
  const value = new Date(0);
  value.setUTCFullYear(year, month, 0);
  value.setUTCHours(0, 0, 0, 0);
  return value.getUTCDate();
}

function assertValidWallClockParts(parts: ZonedParts) {
  const values = [parts.year, parts.month, parts.day, parts.hour, parts.minute, parts.second];
  if (values.some((value) => !Number.isInteger(value))) throw new Error("Invalid scheduled time");
  if (parts.year < 1 || parts.year > 9999) throw new Error("Invalid scheduled time");
  if (parts.month < 1 || parts.month > 12) throw new Error("Invalid scheduled time");
  if (parts.day < 1 || parts.day > daysInMonth(parts.year, parts.month)) throw new Error("Invalid scheduled time");
  if (parts.hour < 0 || parts.hour > 23) throw new Error("Invalid scheduled time");
  if (parts.minute < 0 || parts.minute > 59) throw new Error("Invalid scheduled time");
  if (parts.second < 0 || parts.second > 59) throw new Error("Invalid scheduled time");
}

/** Converts a wall-clock date in the configured schedule timezone to an instant. */
export function createScheduledDate(
  parts: Pick<ZonedParts, "year" | "month" | "day" | "hour" | "minute">,
  timeZone = DEFAULT_SCHEDULE_TIME_ZONE,
): Date {
  const requested: ZonedParts = { ...parts, second: 0 };
  assertValidWallClockParts(requested);
  const estimate = utcFromParts(requested);
  const observed = getScheduleTimeParts(new Date(estimate), timeZone);
  const corrected = estimate - (utcFromParts(observed) - estimate);
  const date = new Date(corrected);
  const correctedParts = getScheduleTimeParts(date, timeZone);
  if (Object.keys(requested).some((key) => requested[key as keyof ZonedParts] !== correctedParts[key as keyof ZonedParts])) {
    throw new Error("Invalid scheduled time");
  }
  return date;
}

/** Parses timezone-less input as Shanghai local time; offset-bearing ISO values remain absolute instants. */
export function parseScheduledDate(value: string, timeZone = DEFAULT_SCHEDULE_TIME_ZONE): Date {
  const text = String(value || "").trim();
  const wallClock = text.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})[T ](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/);
  if (wallClock) {
    const [, year, month, day, hour, minute, second = "0", fraction = ""] = wallClock;
    const requested = {
      year: Number(year),
      month: Number(month),
      day: Number(day),
      hour: Number(hour),
      minute: Number(minute),
      second: Number(second),
    };
    assertValidWallClockParts(requested);
    const date = createScheduledDate(requested, timeZone);
    date.setUTCSeconds(requested.second, Number(fraction.padEnd(3, "0") || 0));
    return date;
  }

  const zoned = text.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?(Z|[+-]\d{2}:?\d{2})$/i);
  if (!zoned) throw new Error("Invalid scheduled time");
  const [, year, month, day, hour, minute, second = "0", , zone] = zoned;
  assertValidWallClockParts({
    year: Number(year),
    month: Number(month),
    day: Number(day),
    hour: Number(hour),
    minute: Number(minute),
    second: Number(second),
  });
  const offset = zone.match(/^([+-])(\d{2}):?(\d{2})$/);
  if (offset && (Number(offset[2]) > 23 || Number(offset[3]) > 59)) throw new Error("Invalid scheduled time");
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) throw new Error("Invalid scheduled time");
  return date;
}

export function addScheduleCalendarDays(parts: Pick<ZonedParts, "year" | "month" | "day">, days: number) {
  const value = new Date(Date.UTC(parts.year, parts.month - 1, parts.day + days));
  return { year: value.getUTCFullYear(), month: value.getUTCMonth() + 1, day: value.getUTCDate() };
}

export function formatScheduledDate(value: Date | string | number, timeZone = DEFAULT_SCHEDULE_TIME_ZONE) {
  const parts = getScheduleTimeParts(value, timeZone);
  return `${parts.year}-${String(parts.month).padStart(2, "0")}-${String(parts.day).padStart(2, "0")} ${String(parts.hour).padStart(2, "0")}:${String(parts.minute).padStart(2, "0")}`;
}
