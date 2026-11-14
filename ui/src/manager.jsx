import React, { useEffect, useMemo, useRef, useState } from "react";
import { issue } from "./util.js";
import { action, agentFor, firstSentence, friendly, cap, headline, light, localDayMinute, name } from "./plain.js";
import { Approval, Light } from "./story.jsx";

const STATE = {
  o: { label: "Guest in room", cls: "s-o" },
  d: { label: "Empty, needs cleaning", cls: "s-d" },
  c: { label: "Being cleaned", cls: "s-c" },
  r: { label: "Empty and ready", cls: "s-r" },
  "?": { label: "No reading", cls: "s-u" },
};
const PRIO = { critical: 3, high: 2, medium: 1, low: 0 };
const END = 23 * 60 + 45;
const hhmm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const needsYou = (d) => d.autonomy !== "autonomous" || d.priority === "critical";
const longDay = (k) => new Date(`${k}T12:00:00Z`).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const shortDay = (k) => new Date(`${k}T12:00:00Z`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });

/**
 * Building at one moment: each room coloured by its state, with a bell if something waits for the
 * manager. ``tilt`` draws the same floors as a stacked, tilted view (CSS only, no 3D model).
 */
function LiveMap({ rooms, states, slot, bells, onRoom, tilt }) {
  const floors = [...new Set(rooms.map((r) => r.floor))].sort((a, b) => b - a);
  const cell = (r) => {
    const s = (states?.[r.id] || "")[slot] || "?";
    return (
      <button key={r.id} className={`room live ${STATE[s].cls}${bells.has(r.id) ? " ring" : ""}`} title={`Room ${r.id.slice(1)}: ${STATE[s].label}`} onClick={() => onRoom(r.id)}>
        <span className="rno">{r.id.slice(1)}</span>
        {bells.has(r.id) && <span className="bell">🔔</span>}
      </button>
    );
  };
  const rowsOf = (f) => {
    const on = rooms.filter((r) => r.floor === f).sort((a, b) => a.id.localeCompare(b.id));
    return (
      <div className="rows">
        <div className="row">{on.filter((r) => r.orientation === "N").map(cell)}</div>
        <div className="corridor" />
        <div className="row">{on.filter((r) => r.orientation === "S").map(cell)}</div>
      </div>
    );
  };
  const legend = (
    <div className="legend">
      {["o", "d", "c", "r"].map((k) => <span key={k}><i className={`swatch ${STATE[k].cls}`} />{STATE[k].label}</span>)}
      <span>🔔 waits for you</span>
    </div>
  );
  if (tilt)
    return (
      <div>
        <div className="iso-wrap">
          <div className="iso">
            {[...floors].reverse().map((f, i) => (
              <div className="iso-floor" key={f} style={{ transform: `translateZ(${i * 150}px)` }}>
                <span className="iso-label">Floor {f}</span>
                {rowsOf(f)}
              </div>
            ))}
          </div>
        </div>
        {legend}
      </div>
    );
  return (
    <div className="bmap">
      {floors.map((f) => (
        <div className="floor" key={f}>
          <div className="floor-label"><b>{f}</b><span>floor</span></div>
          {rowsOf(f)}
        </div>
      ))}
      {legend}
    </div>
  );
}

/** "Live day": what one person running the hotel sees, replayed through a chosen day. */
export function Manager({ data, tz, onOpen, answers, onAnswer }) {
  // every decision with its incident, local day and minute
  const all = useMemo(() => data.episodes.flatMap((e) => e.decisions.map((d) => ({ d, e, ...localDayMinute(d.timestamp_utc, tz) }))), [data, tz]);
  // every day of the replay: from the room-state data if present, otherwise from the decisions
  const days = useMemo(() => [...new Set([...Object.keys(data.days || {}), ...all.map((x) => x.day)])].sort(), [data, all]);
  const stale = !data.days;
  // per day: [items that need a human, all actions]; the eventful days are offered first
  const score = useMemo(() => {
    const s = {};
    for (const x of all) {
      const v = (s[x.day] ||= [0, 0]);
      if (needsYou(x.d)) v[0] += 1;
      v[1] += 1;
    }
    return s;
  }, [all]);
  const busy = useMemo(() => Object.entries(score).sort((a, b) => b[1][0] - a[1][0] || b[1][1] - a[1][1]).slice(0, 5).map(([k]) => k), [score]);

  const [day, setDay] = useState(busy[0] || days[0]);
  const startAt = (() => {
    const m = /^(\d{1,2}):(\d{2})$/.exec(new URLSearchParams(location.search).get("t") || "");
    return m ? Number(m[1]) * 60 + Math.floor(Number(m[2]) / 15) * 15 : 6 * 60;
  })();
  const [minute, setMinute] = useState(startAt);
  const [playing, setPlaying] = useState(false);
  const [tilt, setTilt] = useState(new URLSearchParams(location.search).get("map") === "3d");
  const [speed, setSpeed] = useState(1);  // 1x = a quarter hour every 0.8 s, about a minute for a full day
  const [toasts, setToasts] = useState([]);
  const last = useRef(minute);
  const pick = (k) => { if (!days.includes(k)) return; setDay(k); setMinute(6 * 60); setPlaying(false); };

  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }  // keep a start time given in the URL
    pick(busy[0] || days[0]);
  }, [data.meta.profile]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!playing) return undefined;
    const t = setInterval(() => setMinute((m) => Math.min(m + 15, END)), 800 / speed);
    return () => clearInterval(t);
  }, [playing, speed]);
  useEffect(() => { if (playing && minute >= END) setPlaying(false); }, [minute, playing]);

  const today = all.filter((x) => x.day === day).sort((a, b) => a.minute - b.minute || (a.d.timestamp_utc < b.d.timestamp_utc ? -1 : 1));
  const sofar = today.filter((x) => x.minute <= minute);
  const upcoming = today.find((x) => x.minute > minute);

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
  const answered = sofar.filter((x) => answers[x.d.event_id]);
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

  return (
    <div className="manager">
      <div className="page-head">
        <h1>A day in the building</h1>
        <p>Pick a day, then play it. The building, the agents' actions and the approvals waiting for the duty manager update as the clock moves.</p>
      </div>

      <div className="card daycard">
        <div className="eyebrow">1 · Pick a day</div>
        <div className="dayrow">
          {busy.map((k) => (
            <button key={k} className="daybtn" aria-pressed={k === day} onClick={() => pick(k)}>
              <b>{shortDay(k)}</b>
              <span>{score[k][1]} actions · {score[k][0]} need a person</span>
            </button>
          ))}
          <div className="anyday">
            <span className="muted small">or any day</span>
            <div>
              <button className="chip" onClick={() => pick(days[di - 1])} disabled={di <= 0} aria-label="Previous day">←</button>
              <input type="date" value={day} min={days[0]} max={days[days.length - 1]} onChange={(ev) => pick(ev.target.value)} />
              <button className="chip" onClick={() => pick(days[di + 1])} disabled={di >= days.length - 1} aria-label="Next day">→</button>
            </div>
          </div>
        </div>

        <div className="eyebrow" style={{ marginTop: 18 }}>2 · Play it</div>
        <div className="player">
          <button className="play" onClick={() => { if (minute >= END) setMinute(0); setPlaying(!playing); }} aria-label={playing ? "Pause" : "Play"}>{playing ? "❚❚" : "▶"}</button>
          <div className="now">
            <div className="time">{hhmm(minute)}</div>
            <div className="muted small">{longDay(day)}</div>
          </div>
          <div className="track">
            <div className="marks">
              {today.map((x) => <i key={x.d.event_id} className={needsYou(x.d) ? "hot" : ""} style={{ left: `${(x.minute / END) * 100}%` }} title={`${hhmm(x.minute)} · ${action(x.d.action).label}`} />)}
            </div>
            <input type="range" min={0} max={95} value={slot} onChange={(ev) => { setPlaying(false); setMinute(Number(ev.target.value) * 15); }} aria-label="Time of day" />
            <div className="hours"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>
          </div>
          <div className="seg" role="group" aria-label="Playback speed">
            {[1, 2, 4].map((v) => <button key={v} aria-pressed={speed === v} onClick={() => setSpeed(v)}>{v}×</button>)}
          </div>
          <button className="chip" disabled={!upcoming} onClick={() => { setPlaying(false); setMinute(Math.min(END, Math.ceil(upcoming.minute / 15) * 15)); }}>
            {upcoming ? `Jump to next action · ${hhmm(upcoming.minute)}` : "No more actions today"}
          </button>
        </div>
        <div className="muted small">Each mark on the line is an action by Steward; amber marks need a person. Steward only knows what had happened by {hhmm(minute)}.</div>
      </div>

      {stale && (
        <div className="card callout plain">
          Room states and staff for the replay are not loaded (file <code>data/&lt;dataset&gt;.days.json</code> missing).
          Dates, the inbox and the agent feed still work. Run <code>make ui-data</code> to create it, then reload.
        </div>
      )}
      <div className="card brief">
        <h2>{greet}. Steward handled <b>{handled.length}</b> thing{handled.length === 1 ? "" : "s"} on its own so far. {inbox.length ? <span className="need">{inbox.length} need{inbox.length === 1 ? "s" : ""} you.</span> : "Nothing needs you right now."}</h2>
        <div className="facts">
          <span>🛬 {info.arrivals} arrivals</span>
          <span>🧳 {info.departures} departures</span>
          <span>🧹 {cleaners.length - absent.length} of {cleaners.length} cleaners on shift{absent.length ? <b className="warn"> · {absent.map((s) => name(s.id)).join(", ")} absent</b> : ""}</span>
          <span>🤖 {robots.length} robots available</span>
        </div>
      </div>

      <div className="mgr-grid">
        <div className="card">
          <div className="rowhead">
            <h2>Building at {hhmm(minute)}</h2>
            <div className="seg" role="group">
              <button aria-pressed={!tilt} onClick={() => setTilt(false)}>Floor plan</button>
              <button aria-pressed={tilt} onClick={() => setTilt(true)}>3D view</button>
            </div>
          </div>
          <LiveMap rooms={data.rooms} states={info.rooms} slot={slot} bells={bells} tilt={tilt}
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
                    <Approval d={d} answers={answers} onAnswer={onAnswer} />
                    <button className="link" onClick={() => onOpen(e.id)}>Details →</button>
                  </div>
                </div>
              );
            })}
            {answered.length > 0 && (
              <div className="answered">
                {answered.map(({ d, e }) => <div key={d.event_id} className="small"><span className="muted">{action(d.action).label} · {headline(e)}</span> <Approval d={d} answers={answers} onAnswer={onAnswer} /></div>)}
              </div>
            )}
          </div>

          <div className="card">
            <h2>What the agents did today</h2>
            {sofar.length === 0 ? <div className="empty small">Nothing yet at {hhmm(minute)}. Press ▶ or jump to the next action.</div> : (
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
