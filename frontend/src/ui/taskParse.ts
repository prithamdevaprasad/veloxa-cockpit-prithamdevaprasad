// Turn a typed task request ("Create a task to confirm the GA date, assign it to Alex, due Friday") into form
// values. Deterministic and local: it only fills the form, which the person checks; nothing is written from it.
// An assignee is only a name to look up in Jira's own assignable list; "me" / "my account" is never guessed.

export interface ParsedTask {
  summary?: string; key?: string; due?: string; start?: string; assigneeName?: string; assigneeSelf?: boolean;
}

const DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
const MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
  "november", "december"];
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const DATE = String.raw`(\d{4}-\d{2}-\d{2}|today|tomorrow|(?:next\s+)?(?:mon|tues|wednes|thurs|fri|satur|sun)day|in\s+\d+\s+(?:days?|weeks?)|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?:\s+\d{4})?|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?|end of (?:the )?(?:week|month))`;

export function parseDate(text: string, today: Date): string | undefined {
  const t = text.trim().toLowerCase();
  const d = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  if (/^\d{4}-\d{2}-\d{2}$/.test(t)) return t;
  if (t === "today") return iso(d);
  if (t === "tomorrow") { d.setDate(d.getDate() + 1); return iso(d); }
  let m = /^in\s+(\d+)\s+(day|week)s?$/.exec(t);
  if (m) { d.setDate(d.getDate() + Number(m[1]) * (m[2] === "week" ? 7 : 1)); return iso(d); }
  if (t === "end of week" || t === "end of the week") { d.setDate(d.getDate() + ((5 - d.getDay() + 7) % 7)); return iso(d); }
  if (t === "end of month" || t === "end of the month") return iso(new Date(d.getFullYear(), d.getMonth() + 1, 0));
  m = /^(next\s+)?(\w+day)$/.exec(t);
  if (m && DAYS.includes(m[2])) {
    let add = (DAYS.indexOf(m[2]) - d.getDay() + 7) % 7 || 7;
    if (m[1] && add < 7) add += 7;
    d.setDate(d.getDate() + add); return iso(d);
  }
  m = /^(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)(?:\s+(\d{4}))?$/.exec(t) ?? null;
  let day: number | undefined, mon: number | undefined, year: number | undefined;
  if (m) { day = Number(m[1]); mon = MONTHS.findIndex((x) => x.startsWith(m![2].slice(0, 3))); year = m[3] ? Number(m[3]) : undefined; }
  else {
    const n = /^([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?$/.exec(t);
    if (n) { mon = MONTHS.findIndex((x) => x.startsWith(n[1].slice(0, 3))); day = Number(n[2]); year = n[3] ? Number(n[3]) : undefined; }
  }
  if (day && mon !== undefined && mon >= 0) {
    const out = new Date(year ?? d.getFullYear(), mon, day);
    if (!year && out < d) out.setFullYear(out.getFullYear() + 1);
    return iso(out);
  }
  return undefined;
}

export function parseTaskRequest(text: string, today = new Date()): ParsedTask {
  const out: ParsedTask = {};
  let rest = ` ${text.replace(/\s+/g, " ").trim()} `;
  const key = /\b([A-Z][A-Z0-9_]{0,19}-\d{1,9})\b/.exec(rest);
  if (key) { out.key = key[1]; rest = rest.replace(key[0], " "); }
  const take = (re: RegExp) => { const m = re.exec(rest); if (m) rest = rest.replace(m[0], " "); return m; };
  const start = take(new RegExp(String.raw`[,;]?\s*\b(?:start(?:s|ing)?(?:\s+on)?|begin(?:ning)?|from)\s+` + DATE + String.raw`\b`, "i"));
  if (start) out.start = parseDate(start[1], today);
  const due = take(new RegExp(String.raw`[,;]?\s*\b(?:due(?:\s+(?:on|by))?|by|before|deadline(?:\s+is)?|until)\s+` + DATE + String.raw`\b`, "i"));
  if (due) out.due = parseDate(due[1], today);
  const who = take(/[,;.]?\s*\b(?:and\s+)?(?:assign(?:ed)?|give)\s+(?:it\s+|this\s+|the task\s+)?to\s+([^,;.]+?)(?=\s*(?:[,;.]|$|\bdue\b|\bby\b|\bstart))/i);
  if (who) {
    const name = who[1].trim();
    if (/^(me|myself|my\b.*)$/i.test(name)) out.assigneeSelf = true; else out.assigneeName = name;
  }
  let s = rest.trim()
    .replace(/^(?:please\s+)?(?:can you\s+)?(?:create|add|make|open|log|raise|set up|put in)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:jira\s+)?(?:task|ticket|issue|to-?do)\s*(?:in jira\s*)?(?:to|for|:|-|that)?\s*/i, "")
    .replace(/^(?:update|change)\s+(?:the\s+)?(?:issue|ticket|task)?\s*/i, "")
    .replace(/\s+([,;.])/g, "$1").replace(/[\s,;.]+$/, "").trim();
  if (s) out.summary = s.charAt(0).toUpperCase() + s.slice(1);
  return out;
}
