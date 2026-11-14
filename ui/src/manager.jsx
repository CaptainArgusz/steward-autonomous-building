import React, { useEffect, useMemo, useRef, useState } from "react";
import { issue } from "./util.js";
import { action, agentFor, firstSentence, friendly, cap, headline, light, localDayMinute, name } from "./plain.js";
import { Light } from "./story.jsx";

const STATE = {
  o: { label: "Guest in room", cls: "s-o" },
  d: { label: "Empty, needs cleaning", cls: "s-d" },
  c: { label: "Being cleaned", cls: "s-c" },
  r: { label: "Empty and ready", cls: "s-r" },
  "?": { label: "No reading", cls: "s-u" },
};
const PRIO = { critical: 3, high: 2, medium: 1, low: 0 };
const hhmm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const needsYou = (d) => d.autonomy !== "autonomous" || d.priority === "critical";
const longDay = (k) => new Date(`${k}T12:00:00Z`).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });

/** Building at one moment: each room coloured by its state, with a bell if something waits for the manager. */
function LiveMap({ rooms, states, slot, bells, onRoom }) {
  const floors = [...new Set(rooms.map((r) => r.floor))].sort((a, b) => b - a);
  const cell = (r) => {
    const s = (states?.[r.id] || "")[slot] || "?";
    return (
      <button key={r.id} className={`room live ${STATE[s].cls}`} title={`Room ${r.id.slice(1)}: ${STATE[s].label}`} onClick={() => onRoom(r.id)}>
        <span className="rno">{r.id.slice(1)}</span>
        {bells.has(r.id) && <span className="bell">🔔</span>}
      </button>
    );
  };
  return (
    <div className="bmap">
      {floors.map((f) => {
        const on = rooms.filter((r) => r.floor === f).sort((a, b) => a.id.localeCompare(b.id));
        return (
          <div className="floor" key={f}>
            <div className="floor-label"><b>{f}</b><span>floor</span></div>
            <div className="rows">
              <div className="row">{on.filter((r) => r.orientation === "N").map(cell)}</div>
              <div className="corridor" />
              <div className="row">{on.filter((r) => r.orientation === "S").map(cell)}</div>
            </div>
          </div>
        );
      })}
      <div className="legend">
        {["o", "d", "c", "r"].map((k) => <span key={k}><i className={`swatch ${STATE[k].cls}`} />{STATE[k].label}</span>)}
        <span>🔔 waits for you</span>
      </div>
    </div>
  );
}

/** "Duty manager" mode: what one person running the hotel sees, replayed through a chosen day. */
export function Manager({ data, tz, onOpen }) {
  // every decision with its incident, local day and minute
  const all = useMemo(() => data.episodes.flatMap((e) => e.decisions.map((d) => ({ d, e, ...localDayMinute(d.timestamp_utc, tz) }))), [data, tz]);
  // every day of the replay: from the room-state data if present, otherwise from the decisions
  const days = useMemo(() => [...new Set([...Object.keys(data.days || {}), ...all.map((x) => x.day)])].sort(), [data, all]);
  const stale = !data.days;
  // busiest days for the manager: most items that need a human, then most decisions
  const busy = useMemo(() => {
    const score = {};
    for (const x of all) {
      const s = (score[x.day] ||= [0, 0]);
      if (needsYou(x.d)) s[0] += 1;
      s[1] += 1;
    }
    return Object.entries(score)
      .sort((a, b) => b[1][0] - a[1][0] || b[1][1] - a[1][1]).slice(0, 6).map(([k]) => k);
  }, [all, data]);

  const [day, setDay] = useState(busy[0] || days[0]);
  const startAt = (() => {
    const m = /^(\d{1,2}):(\d{2})$/.exec(new URLSearchParams(location.search).get("t") || "");
    return m ? Number(m[1]) * 60 + Math.floor(Number(m[2]) / 15) * 15 : 6 * 60;
  })();
  const [minute, setMinute] = useState(startAt);
  const [playing, setPlaying] = useState(false);
  const [answers, setAnswers] = useState({});
  const [toasts, setToasts] = useState([]);
  const last = useRef(minute);

  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }  // keep a start time given in the URL
    setDay(busy[0] || days[0]); setMinute(6 * 60); setPlaying(false); setAnswers({});
  }, [data.meta.profile]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!playing) return undefined;
    const t = setInterval(() => setMinute((m) => Math.min(m + 15, 23 * 60 + 45)), 220);
    return () => clearInterval(t);
  }, [playing]);
  useEffect(() => { if (playing && minute >= 23 * 60 + 45) setPlaying(false); }, [minute, playing]);

  const today = all.filter((x) => x.day === day).sort((a, b) => a.minute - b.minute || (a.d.timestamp_utc < b.d.timestamp_utc ? -1 : 1));
  const sofar = today.filter((x) => x.minute <= minute);

  // notifications: while playing, anything that needs the manager pops up when the clock passes it
  useEffect(() => {
    const from = last.current;
    last.current = minute;
    if (!playing || minute < from) return;
    const fresh = today.filter((x) => x.minute > from && x.minute <= minute && needsYou(x.d));
    if (!fresh.length) return;
    setToasts((t) => [...t, ...fresh.map((x) => ({ id: x.d.event_id, x }))].slice(-3));
    const ids = fresh.map((x) => x.d.event_id);
    setTimeout(() => setToasts((t) => t.filter((y) => !ids.includes(y.id))), 4500);
  }, [minute]); // eslint-disable-line react-hooks/exhaustive-deps

  const inbox = sofar.filter((x) => needsYou(x.d) && !answers[x.d.event_id])
    .sort((a, b) => (PRIO[b.d.priority] ?? 0) - (PRIO[a.d.priority] ?? 0) || a.minute - b.minute);
  const handled = sofar.filter((x) => !needsYou(x.d));
  const bells = new Set(inbox.flatMap((x) => x.e.rooms));
  const info = data.days?.[day] || { rooms: {}, staff: [], arrivals: "–", departures: "–" };
  const slot = Math.floor(minute / 15);
  const at = (iso) => localDayMinute(iso, tz);
  const knownNow = (s) => !s.known || at(s.known).day < day || (at(s.known).day === day && at(s.known).minute <= minute);
  const cleaners = info.staff.filter((s) => s.role === "cleaner");
  const absent = cleaners.filter((s) => s.status !== "scheduled" && knownNow(s));
  const robots = info.staff.filter((s) => s.role === "robot" && (s.status === "scheduled" || !knownNow(s)));
  const greet = minute < 12 * 60 ? "Good morning" : minute < 18 * 60 ? "Good afternoon" : "Good evening";
  const di = days.indexOf(day);
  const step = (k) => { setDay(days[Math.max(0, Math.min(days.length - 1, di + k))]); setMinute(6 * 60); setPlaying(false); setAnswers({}); };

  return (
    <div className="manager">
      <div className="mgr-top">
        <div className="clock">
          <button className="play" onClick={() => { if (minute >= 23 * 60 + 45) setMinute(0); setPlaying(!playing); }}>{playing ? "❚❚" : "▶"}</button>
          <div>
            <div className="time">{hhmm(minute)}</div>
            <div className="muted small">{longDay(day)} · local time</div>
          </div>
          <input type="range" min={0} max={95} value={slot} onChange={(ev) => { setPlaying(false); setMinute(Number(ev.target.value) * 15); }} />
        </div>
        <div className="daypick">
          <button className="chip" onClick={() => step(-1)} disabled={di <= 0}>← day</button>
          <select value={day} onChange={(ev) => { setDay(ev.target.value); setMinute(6 * 60); setPlaying(false); setAnswers({}); }}>
            {days.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <button className="chip" onClick={() => step(1)} disabled={di >= days.length - 1}>day →</button>
          <span className="muted small">Busy days:</span>
          {busy.map((k) => (
            <button key={k} className="chip" aria-pressed={k === day} onClick={() => { setDay(k); setMinute(6 * 60); setPlaying(false); setAnswers({}); }}>
              {new Date(`${k}T12:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}
            </button>
          ))}
        </div>
      </div>

      {stale && (
        <div className="card callout">
          Room states and staff for the replay are not loaded (file <code>data/&lt;dataset&gt;.days.json</code> missing).
          Dates, the inbox and the agent feed still work. Run <code>make ui-data</code> to create it, then reload.
        </div>
      )}
      <div className="card brief">
        <h2>{greet}. {inbox.length ? <>Steward handled <b>{handled.length}</b> thing{handled.length === 1 ? "" : "s"} on its own today. <span className="need">{inbox.length} need{inbox.length === 1 ? "s" : ""} you.</span></> : <>Steward handled <b>{handled.length}</b> thing{handled.length === 1 ? "" : "s"} on its own today. Nothing needs you right now.</>}</h2>
        <div className="facts">
          <span>🛬 {info.arrivals} arrivals</span>
          <span>🧳 {info.departures} departures</span>
          <span>🧹 {cleaners.length - absent.length} of {cleaners.length} cleaners on shift{absent.length ? <b className="warn"> · {absent.map((s) => name(s.id)).join(", ")} absent</b> : ""}</span>
          <span>🤖 {robots.length} robots available</span>
        </div>
        <div className="muted small">Replay of PLATO-40: Steward only knows what had happened by {hhmm(minute)}. Approvals here are simulated.</div>
      </div>

      <div className="mgr-grid">
        <div className="card">
          <h2>Building at {hhmm(minute)}</h2>
          <LiveMap rooms={data.rooms} states={info.rooms} slot={slot} bells={bells}
            onRoom={(r) => { const x = sofar.filter((y) => y.e.rooms.includes(r)).pop(); if (x) onOpen(x.e.id); }} />
        </div>

        <div className="mgr-side">
          <div className="card">
            <h2>Needs you <span className={`count${inbox.length ? "" : " zero"}`}>{inbox.length}</span></h2>
            {inbox.length === 0 ? <div className="empty small">Nothing waiting. Steward is handling the rest.</div> : inbox.map(({ d, e, minute: m }) => {
              const ag = agentFor(e.issue_type, d.action);
              return (
                <div key={d.event_id} className={`ask ${light(d.autonomy).cls}`}>
                  <div className="ask-head"><Light autonomy={d.autonomy} /><span className="muted small">{hhmm(m)} · {d.priority} priority</span></div>
                  <div><b>{action(d.action).icon} {action(d.action).label}</b> · {headline(e)}</div>
                  <div className="muted small">{ag.icon} {ag.name}: {cap(firstSentence(friendly(d.reason, tz)))}</div>
                  <div className="ask-btns">
                    <button className="chip primary" onClick={() => setAnswers({ ...answers, [d.event_id]: "approved" })}>Approve</button>
                    <button className="chip" onClick={() => setAnswers({ ...answers, [d.event_id]: "rejected" })}>Reject</button>
                    <button className="link" onClick={() => onOpen(e.id)}>Details →</button>
                  </div>
                </div>
              );
            })}
            {Object.keys(answers).length > 0 && (
              <div className="muted small answered">
                {Object.entries(answers).map(([id, a]) => <div key={id}>{a === "approved" ? "✓ Approved" : "✕ Rejected"} (simulated) · {action(today.find((x) => x.d.event_id === id)?.d.action || "").label}</div>)}
              </div>
            )}
          </div>

          <div className="card">
            <h2>What the agents did today</h2>
            {sofar.length === 0 ? <div className="empty small">Nothing yet today. Press ▶ to play the day.</div> : (
              <ol className="feed">
                {[...sofar].reverse().map(({ d, e, minute: m }) => {
                  const ag = agentFor(e.issue_type, d.action);
                  return (
                    <li key={d.event_id} className={needsYou(d) ? "hot" : ""} onClick={() => onOpen(e.id)}>
                      <span className="when">{hhmm(m)}</span>
                      <span className="ag" title={ag.name}>{ag.icon}</span>
                      <div>
                        <div><b>{ag.name}</b> · {action(d.action).label} <span className="muted">· {headline(e)}</span></div>
                        <div className="muted small">{cap(firstSentence(friendly(d.reason, tz)))}</div>
                      </div>
                      <span className={`dotlight ${light(d.autonomy).cls}`} title={light(d.autonomy).label} />
                    </li>
                  );
                })}
              </ol>
            )}
          </div>
        </div>
      </div>

      <div className="toasts">
        {toasts.map(({ id, x }) => (
          <div key={id} className="toast" onClick={() => onOpen(x.e.id)}>
            <div className="muted small">🔔 {hhmm(x.minute)} · {issue(x.e.issue_type).label}</div>
            <b>{action(x.d.action).icon} {action(x.d.action).label}</b> · {headline(x.e)}
            <div className="small"><Light autonomy={x.d.autonomy} /></div>
          </div>
        ))}
      </div>
    </div>
  );
}
