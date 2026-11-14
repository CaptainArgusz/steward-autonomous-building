export const ISSUES = {
  room_turnaround: { label: "Checkout conflict", cls: "t-room_turnaround", color: "var(--turn)" },
  water_leak: { label: "Water leak", cls: "t-water_leak", color: "var(--water)" },
  heating_comfort: { label: "Heating / comfort", cls: "t-heating_comfort", color: "var(--heat)" },
  ventilation_maintenance: { label: "Ventilation / sensors", cls: "t-ventilation_maintenance", color: "var(--vent)" },
  occupancy_surge: { label: "Weekend capacity", cls: "t-occupancy_surge", color: "var(--surge)" },
  guest_request: { label: "Guest reply", cls: "t-guest_request", color: "var(--guest, #8a8f98)" },
  logistics: { label: "Logistics", cls: "t-logistics", color: "var(--logi)" },
  energy_optimization: { label: "Energy plan", cls: "t-energy_optimization", color: "var(--energy, #5b6b8c)" },
};
export const issue = (t) => ISSUES[t] || { label: t.replaceAll("_", " "), cls: "t-other", color: "var(--other)" };

export const AUTONOMY = {
  autonomous: "Autonomous",
  human_approval: "Needs human approval",
  approval_and_consent: "Needs approval + guest consent",
};
export const STATUS = {
  executed_simulated: "Executed (simulated)",
  pending_human_approval: "Waiting for approval",
  pending_human_approval_and_guest_consent: "Waiting for approval and guest consent",
  blocked_by_dependency: "Waiting for an earlier step",
  refused_safety_check: "Refused by a safety check",
};

export function fmtTime(iso, tz = "Europe/Vienna") {
  return new Date(iso).toLocaleString("en-GB", {
    timeZone: tz, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}
export function fmtDay(iso, tz = "Europe/Vienna") {
  return new Date(iso).toLocaleDateString("en-GB", { timeZone: tz, weekday: "short", day: "2-digit", month: "short" });
}

/** Sequential blue ramp, light = low. Returns [background, text colour]. */
const RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#104281"];
export function rampColor(v, lo, hi) {
  if (v == null || Number.isNaN(v)) return [null, null];
  const f = hi === lo ? 0.5 : (v - lo) / (hi - lo);
  const i = Math.max(0, Math.min(RAMP.length - 1, Math.floor(f * RAMP.length)));
  return [RAMP[i], i >= 3 ? "#ffffff" : "#0b0b0b"];
}
