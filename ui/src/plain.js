// Plain-language layer: turns contract codes into words a non-technical reader understands.
// Every rule here is generic (by action, ID pattern or file name); nothing is written for one incident.

import { fmtTime } from "./util.js";

/** What each action means, who carries it out, and an icon. */
export const ACTIONS = {
  schedule_cleaning: { icon: "🧹", label: "Schedule cleaning", who: "Housekeeping" },
  dispatch_robot: { icon: "🤖", label: "Send a robot", who: "Robot" },
  adjust_room_setpoint: { icon: "🌡️", label: "Change room temperature", who: "Building control" },
  optimize_energy: { icon: "🔋", label: "Plan battery and energy use", who: "Building control" },
  block_room: { icon: "⛔", label: "Block a room", who: "Manager" },
  release_room: { icon: "🔑", label: "Release a room to the guest", who: "Housekeeping" },
  reassign_room: { icon: "🔁", label: "Move guest to another room", who: "Front office" },
  request_inspection: { icon: "🔍", label: "Ask someone to inspect", who: "Staff on site" },
  dispatch_technician: { icon: "👷", label: "Call a technician", who: "Technician" },
  order_service: { icon: "📦", label: "Order an external service", who: "External service" },
  notify_guest: { icon: "💬", label: "Message the guest", who: "Guest messaging" },
  flag_anomaly: { icon: "🚩", label: "Flag something unusual", who: "Operations team" },
  defer_action: { icon: "⏸️", label: "Wait and watch", who: "Steward" },
  request_human_approval: { icon: "✋", label: "Ask a manager to decide", who: "Manager" },
};
export const action = (a) => ACTIONS[a] || { icon: "•", label: a.replaceAll("_", " "), who: "Operations" };

/** Traffic light for the autonomy level. */
export const LIGHT = {
  autonomous: { cls: "go", label: "Done automatically", short: "Automatic", note: "Simulated - nothing real was switched or sent." },
  human_approval: { cls: "wait", label: "Waiting for a human", short: "Needs approval", note: "Steward proposes it; a person has to approve." },
  approval_and_consent: { cls: "stop", label: "Needs approval and guest consent", short: "Approval + consent", note: "A person must approve and the guest must agree." },
};
export const light = (a) => LIGHT[a] || LIGHT.human_approval;

/** Readable name for any ID used in the data. */
export function name(id) {
  let m;
  if ((m = /^R(\d)(\d{2})$/.exec(id))) return `Room ${m[1]}${m[2]}`;
  if ((m = /^WM_R(\d{3})$/.exec(id))) return `Water meter, room ${m[1]}`;
  if ((m = /^WASTE_F(\d)$/.exec(id))) return `Floor ${m[1]} bin`;
  if ((m = /^GW_F(\d)$/.exec(id))) return `Floor ${m[1]} sensor gateway`;
  if ((m = /^ROBOT_(\d+)$/.exec(id))) return `Robot ${m[1]}`;
  if ((m = /^CLEANER_(\d+)$/.exec(id))) return `Cleaner ${m[1]}`;
  if ((m = /^TECH_(\d+)$/.exec(id))) return `Technician`;
  if ((m = /^ONCALL_(\d+)$/.exec(id))) return `On-call housekeeper`;
  const fixed = {
    WASTE_MAIN: "Central waste container", VEND1: "Vending machine", BAT1: "Battery", PV1: "Solar panels",
    HP1: "Heat pump", AHU1: "Ventilation unit", WM_MAIN: "Main water meter", BUILDING: "Whole building",
    MAIN_ENTRANCE: "Main entrance", ACS1: "Access control",
  };
  return fixed[id] || id;
}

/** "Room 304, Room 305 and 4 more" */
export function names(ids, max = 3) {
  const rooms = ids.filter((i) => /^R\d{3}$/.test(i));
  const other = ids.filter((i) => !/^R\d{3}$/.test(i));
  const parts = [];
  if (rooms.length === 1) parts.push(name(rooms[0]));
  else if (rooms.length > 1) parts.push(`Rooms ${rooms.map((r) => r.slice(1)).join(", ")}`);
  parts.push(...other.map(name));
  if (parts.length <= max) return parts.join(" · ");
  return `${parts.slice(0, max).join(" · ")} +${parts.length - max} more`;
}

const SOURCES = {
  "bookings.csv": "Bookings", "access_log.csv": "Door access log", "room_telemetry.csv": "Room sensors",
  "room_water.csv": "Room water meter", "water_main.csv": "Main water meter", "energy.csv": "Energy meters",
  "weather.csv": "Weather station", "weather_forecast.csv": "Weather forecast", "tariff.csv": "Electricity prices",
  "equipment.csv": "Plant sensors", "logistics.csv": "Bin and vending sensors", "staff_roster.csv": "Staff roster",
  "work_orders.csv": "Maintenance log", "guest_requests.csv": "Guest requests", "events.jsonl": "Operations log",
  "building.json": "Building data", "decisions": "Earlier decision",
};

const ISO = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z/g;

/** Replace ISO timestamps in free text with local, friendly times. */
export function friendly(text, tz) {
  if (!text) return text;
  return text
    .replace(/(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z)\.\.(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z)/g, (_, a, b) => `${fmtTime(a, tz)} to ${fmtTime(b, tz)}`)
    .replace(ISO, (s) => fmtTime(s, tz))
    .replace(/\b(R)(\d{3})\b/g, "room $2")
    .replace(/\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b|\b(?:HP1|AHU1|BAT1|PV1|VEND1)\b/g, (id) => {
      const n = name(id);
      return n === id ? id : n;
    })
    .replace(/\bAHU\b/g, "ventilation unit")
    // snake_case event and field names from the data: "cleaning_completed" -> "cleaning completed"
    .replace(/\b[a-z]+(?:_[a-z0-9]+)+\b/g, (w) => w.replaceAll("_", " "));
}

/** Capitalise the first letter of a sentence. */
export const cap = (t) => (t ? t.charAt(0).toUpperCase() + t.slice(1) : t);

/** Evidence line -> { source, text } in plain words. Unknown shapes are passed through. */
export function evidence(line, tz) {
  const i = line.indexOf(":");
  const file = i > 0 ? line.slice(0, i) : "";
  const source = SOURCES[file];
  if (!source) return { source: "Note", text: friendly(line, tz) };
  let rest = line.slice(i + 1).trim();
  rest = rest.replace(/^([A-Z][A-Z0-9_]*):([a-z_]+)=([-\d.]+)@/, (_, id, metric, v) => `${name(id)} ${metric.replace("fill_pct", "fill level %").replaceAll("_", " ")} ${v} at `);
  // drop a leading record number (E0016409:, SH001266:, BK002508:) - the text after it is the content
  const rec = /^([A-Z]{1,3}\d{4,})(?::\s*|\s+)(.+)$/.exec(rest);
  if (rec) rest = rec[2];
  else if (/^[A-Z]{1,3}\d{4,}$/.test(rest)) rest = `record ${rest}`;
  rest = rest.replace(/^([A-Z][A-Z0-9_]*):/, (_, id) => `${name(id)}: `);
  return { source, text: cap(friendly(rest, tz)) };
}

/** First sentence of a reason, for one-line summaries. */
export function firstSentence(text) {
  if (!text) return "";
  const m = /^(.+?[.!?])(\s|$)/.exec(text);
  return m ? m[1] : text;
}

/** The decision that best states the problem: a flag, inspection, escalation or guest notice. */
const PROBLEM = ["flag_anomaly", "request_inspection", "notify_guest", "schedule_cleaning", "request_human_approval"];
export function problem(decisions) {
  for (const a of PROBLEM) {
    const d = decisions.find((x) => x.action === a);
    if (d) return d;
  }
  return decisions[0];
}

/** Story headline for an incident, built from its type and assets. */
export function headline(e) {
  const where = names(e.assets.filter((a) => !/^(CLEANER|ROBOT|TECH|ONCALL)_/.test(a)), 2);
  return where || names(e.assets, 2);
}

/** The most serious autonomy level in an incident. */
export function strictest(e) {
  const order = ["approval_and_consent", "human_approval", "autonomous"];
  for (const a of order) if (e.decisions.some((d) => d.autonomy === a)) return a;
  return "autonomous";
}

/**
 * Featured cases for the guided tour: one per incident type, chosen by a fixed rule so it works on
 * any dataset. Prefer incidents that came before something the replay later recorded (a guest who
 * waited, a stock-out, ...), then the highest priority, then the richest (most decisions).
 */
export const TOUR_ORDER = [
  "room_turnaround", "water_leak", "heating_comfort", "ventilation_maintenance",
  "occupancy_surge", "logistics", "energy_optimization", "guest_request",
];
/** Replies to guest requests are service, not incidents: kept out of incident counts by default. */
export const isIncident = (e) => e.issue_type !== "guest_request";
const PRIO = { critical: 3, high: 2, medium: 1, low: 0 };
export function featured(episodes, matched = {}) {
  const out = [];
  const types = [...TOUR_ORDER, ...new Set(episodes.map((e) => e.issue_type).filter((t) => !TOUR_ORDER.includes(t)))];
  for (const t of types) {
    const pool = episodes.filter((e) => e.issue_type === t);
    if (!pool.length) continue;
    const score = (e) => [matched[e.id] ? 1 : 0, PRIO[e.priority] ?? 0, Math.min(e.decisions.length, 6), e.charts.length > 0 ? 1 : 0];
    pool.sort((a, b) => {
      const sa = score(a), sb = score(b);
      for (let i = 0; i < sa.length; i++) if (sa[i] !== sb[i]) return sb[i] - sa[i];
      return a.start < b.start ? -1 : 1;
    });
    out.push(pool[0]);
  }
  return out;
}

/** The agent responsible for each kind of incident (guest messages go through Guest service). */
export const AGENTS = {
  room_turnaround: { icon: "🧹", name: "Housekeeping agent" },
  occupancy_surge: { icon: "🧹", name: "Housekeeping agent" },
  water_leak: { icon: "💧", name: "Water agent" },
  heating_comfort: { icon: "🔥", name: "Heating agent" },
  ventilation_maintenance: { icon: "🛠️", name: "Maintenance agent" },
  logistics: { icon: "📦", name: "Logistics agent" },
  energy_optimization: { icon: "🔋", name: "Energy agent" },
  guest: { icon: "💬", name: "Guest service agent" },
  guest_request: { icon: "💬", name: "Guest service agent" },
};
export const agentFor = (issueType, act) =>
  act === "notify_guest" ? AGENTS.guest : AGENTS[issueType] || { icon: "🤖", name: "Operations agent" };

const _parts = {};
/** Local "YYYY-MM-DD" and minute of day for a UTC timestamp. */
export function localDayMinute(iso, tz) {
  const f = (_parts[tz] ||= new Intl.DateTimeFormat("en-GB", {
    timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }));
  const p = Object.fromEntries(f.formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
  return { day: `${p.year}-${p.month}-${p.day}`, minute: Number(p.hour) * 60 + Number(p.minute) };
}
