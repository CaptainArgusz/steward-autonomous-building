import React, { useEffect, useMemo, useState } from "react";
import { STATUS, fmtTime, issue, rampColor } from "./util.js";
import { TOUR_ORDER, featured, headline, isIncident, strictest } from "./plain.js";
import { IncidentStory, Light } from "./story.jsx";
import { Manager } from "./manager.jsx";
import { Overview } from "./overview.jsx";
import { Lessons, preloadLessons } from "./lessons.jsx";
import { Record } from "./record.jsx";

const DATASETS = ["quickstart", "dev", "holdout"];
const VIEWS = [["overview", "Overview"], ["incidents", "Incidents"], ["day", "Live day"], ["lessons", "Lessons"], ["record", "Track record"]];

/**
 * Building map: one band per floor, north rooms above the corridor, south rooms below.
 * A room tile shows its number and how many incidents it had. ``heat`` colours rooms by a finding's
 * value instead; ``focus`` is the small locator that only marks the highlighted rooms.
 */
export function FloorMap({ rooms, episodes = [], selectedRoom, onRoom, heat, highlight = [], focus = false }) {
  const byRoom = useMemo(() => {
    const m = {};
    for (const e of episodes) for (const r of e.rooms) ((m[r] ||= {})[e.issue_type] = (m[r][e.issue_type] || 0) + 1);
    return m;
  }, [episodes]);
  const floors = [...new Set(rooms.map((r) => r.floor))].sort((a, b) => b - a);
  const hv = heat ? Object.values(heat.rooms).filter((v) => v != null) : [];
  const lo = Math.min(...hv), hi = Math.max(...hv);

  const cell = (r) => {
    const [bg, fg] = heat ? rampColor(heat.rooms[r.id], lo, hi) : [null, null];
    const counts = byRoom[r.id] || {};
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    const hl = highlight.includes(r.id);
    const tip = [`Room ${r.id.slice(1)} · floor ${r.floor} · ${r.orientation === "N" ? "north" : "south"} side · ${r.area_m2} m²${r.has_bathtub ? " · bathtub" : ""}`,
      ...Object.entries(counts).map(([t, n]) => `${n} × ${issue(t).label}`)].join("\n");
    return (
      <button key={r.id} className={`room${hl ? " hl" : ""}${focus && !hl ? " dim" : ""}`} aria-pressed={selectedRoom === r.id}
        onClick={() => onRoom && onRoom(selectedRoom === r.id ? null : r.id)} tabIndex={focus ? -1 : 0}
        style={bg ? { background: bg, color: fg } : undefined} title={tip}>
        <span className="rno">{r.id.slice(1)}</span>
        {heat ? <span className="val">{heat.rooms[r.id] != null ? heat.rooms[r.id] : "–"}</span>
          : !focus && total > 0 && <span className="rdot">{total}</span>}
      </button>
    );
  };

  return (
    <div className={`bmap${focus ? " focus" : ""}`}>
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
      {heat ? <div className="ramp"><span>{lo}</span><i /><span>{hi}</span><span>{heat.label}</span></div>
        : !focus && <div className="legend"><span>Top row of each floor faces north.{episodes.length > 0 ? " A number is how many incidents the room had." : ""}</span></div>}
    </div>
  );
}

function Decision({ d, tz }) {
  return (
    <div className="decision">
      <div className="head">
        <code>{d.action}</code>
        <span className={`badge p-${d.priority}`}>{d.priority}</span>
        <Light autonomy={d.autonomy} short />
        <span className="muted small">{fmtTime(d.timestamp_utc, tz)}{d.resource_id ? ` · ${d.resource_id}` : ""}</span>
      </div>
      <div>{d.reason}</div>
      <dl className="kv">
        <dt>Executor</dt><dd>{STATUS[d.status] || d.status}</dd>
        {d.evidence && <><dt>Evidence</dt><dd><ul className="plain">{d.evidence.map((x, i) => <li key={i}>{x}</li>)}</ul></dd></>}
        {d.expected_outcome && <><dt>Expected</dt><dd>{d.expected_outcome}</dd></>}
        {d.verification_plan && <><dt>Verification</dt><dd>{d.verification_plan}</dd></>}
        {d.proposed_message && <><dt>Message{d.message_language ? ` (${d.message_language})` : ""}</dt><dd><div className="msg">{d.proposed_message}</div><span className="muted small">Proposed text, not delivered.</span></dd></>}
        {d.guest_request_ids && <><dt>Requests</dt><dd>{d.guest_request_ids.join(", ")}</dd></>}
        {d.confidence != null && <><dt>Confidence</dt><dd>{Math.round(d.confidence * 100)} %</dd></>}
      </dl>
      {d.time_context?.length > 0 && <details className="time-context"><summary>Information used</summary>
        <ul className="plain small">{d.time_context.map((x, i) => <li key={i}>{x.label}: {x.at ? fmtTime(x.at, tz) : x.value}</li>)}</ul>
      </details>}
    </div>
  );
}

function Technical({ e, tz }) {
  return (
    <div className="techbody">
      <div className="muted small">Incident {e.id} · assets: {e.assets.join(", ")}</div>
      {e.decisions.map((d) => <Decision key={d.event_id} d={d} tz={tz} />)}
      {e.events.length > 0 && (
        <div className="events">
          {e.events.map((x, i) => <div key={i}><span className="muted">{fmtTime(x.t, tz)}</span><span>{x.room} · {x.type.replaceAll("_", " ")}{x.resource ? ` · ${x.resource}` : ""}</span></div>)}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const params = new URLSearchParams(location.search); // ?data=dev&view=incidents&sel=<episode id>
  const [name, setName] = useState(params.get("data") || "dev");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [view, setView] = useState(params.get("view") || (params.get("sel") ? "incidents" : "overview"));
  const [scope, setScope] = useState(params.get("sel") ? "all" : "highlights");
  const [type, setType] = useState(null);
  const [sel, setSel] = useState(params.get("sel"));
  // simulated sign-offs by event id, shared by Incidents and Live day; kept in the browser only
  const [answers, setAnswers] = useState({});
  const onAnswer = (id, a) => setAnswers((cur) => { const n = { ...cur }; if (a) n[id] = a; else delete n[id]; return n; });

  useEffect(() => {
    setData(null); setError(null); setAnswers({}); setType(null);
    if (name !== (params.get("data") || "dev")) setSel(null);
    fetch(`./data/${name}.json`)
      // a dev server answers a missing file with the HTML page, so a parse failure also means "no bundle"
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("missing"))))
      .then((d) => {
        setData(d);
        preloadLessons(d.meta.profile); // start the Lessons report now; it can take a while the first time
        // optional second file with room states per day for the live-day replay
        fetch(`./data/${name}.days.json`)
          .then((r) => (r.ok ? r.json() : null))
          .then((x) => { if (x?.days) setData((cur) => (cur === d ? { ...d, days: x.days } : cur)); })
          .catch(() => {});
      })
      .catch(() => setError(`No data bundle for "${name}" yet. Create it with: uv run steward ui data/${name}`));
  }, [name]);

  const tz = data?.meta.local_timezone;
  const cases = useMemo(() => (data ? featured(data.episodes, data.scorecard?.matched || {}) : []), [data]);
  // the list shows one of: a best example per kind, every incident, or one kind (guest replies only when chosen)
  const shown = !data ? [] : type ? data.episodes.filter((e) => e.issue_type === type)
    : scope === "highlights" ? cases : data.episodes.filter(isIncident);
  const selected = shown.find((e) => e.id === sel) || data?.episodes.find((e) => e.id === sel) || shown[0] || null;

  // arrow keys step through the incident list
  useEffect(() => {
    if (view !== "incidents") return undefined;
    const onKey = (ev) => {
      if (ev.target.closest && ev.target.closest("input, textarea, select")) return;
      const step = ev.key === "ArrowRight" || ev.key === "ArrowDown" ? 1 : ev.key === "ArrowLeft" || ev.key === "ArrowUp" ? -1 : 0;
      if (!step || !shown.length) return;
      ev.preventDefault();
      const i = shown.findIndex((e) => e.id === selected?.id);
      const next = shown[Math.max(0, Math.min(shown.length - 1, i + step))];
      setSel(next.id);
      document.getElementById(`inc-${next.id}`)?.scrollIntoView({ block: "nearest" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    if (view === "incidents" && selected) document.getElementById(`inc-${selected.id}`)?.scrollIntoView({ block: "nearest" });
  }, [view, selected?.id]);

  const go = (v, opts = {}) => {
    setView(v);
    if (v === "incidents") { setType(opts.type || null); setSel(null); setScope(opts.type ? "all" : "highlights"); }
    window.scrollTo(0, 0);
  };
  const open = (id) => { setSel(id); setScope("all"); setType(null); setView("incidents"); window.scrollTo(0, 0); };

  const header = (
    <header className="top">
      <div className="brand"><div className="mark">S</div>Steward <span>· PLATO-40</span></div>
      <nav className="nav">
        {VIEWS.map(([v, label]) => <button key={v} aria-pressed={view === v} onClick={() => go(v)}>{label}</button>)}
      </nav>
      <span className="spacer" />
      <span className="simpill" title="Nothing real was switched and no message was sent. Every execution goes to a mock executor.">Simulated replay</span>
      <select className="ds" value={name} onChange={(ev) => setName(ev.target.value)} aria-label="Dataset">
        {DATASETS.map((d) => <option key={d} value={d}>{d} data</option>)}
      </select>
    </header>
  );
  if (error) return <div className="app">{header}<div className="page"><div className="card empty">{error}</div></div></div>;
  if (!data) return <div className="app">{header}<div className="page"><div className="card empty">Loading…</div></div></div>;

  const types = [...new Set(data.episodes.map((e) => e.issue_type))].sort((x, y) => TOUR_ORDER.indexOf(x) - TOUR_ORDER.indexOf(y));
  const count = (pred) => data.episodes.filter(pred).length;
  const decided = (e) => { const p = e.decisions.filter((d) => d.autonomy !== "autonomous"); return p.length > 0 && p.every((d) => answers[d.event_id]); };
  const pickList = (v) => { setSel(null); setType(v === "highlights" || v === "all" ? null : v); setScope(v === "highlights" ? "highlights" : "all"); };

  return (
    <div className="app">
      {header}

      {view === "overview" && <Overview data={data} go={go} />}

      {view === "day" && <Manager data={data} tz={tz} onOpen={open} answers={answers} onAnswer={onAnswer} />}

      {view === "incidents" && (
        <div className="page">
          <div className="page-head">
            <h1>Incidents</h1>
            <p>Pick a case on the left. On the right, every case is told in the same five steps, from what happened to how success is checked.</p>
          </div>
          <div className="inc">
            <div className="card inc-side">
              <div className="eyebrow">1 · Pick a case</div>
              <div>
                <select className="pick" value={type || scope} onChange={(ev) => pickList(ev.target.value)} aria-label="Which cases to list">
                  <option value="highlights">Best example of each kind ({cases.length})</option>
                  <option value="all">Every incident ({count(isIncident)})</option>
                  <optgroup label="One kind only">
                    {types.map((t) => <option key={t} value={t}>{issue(t).label} ({count((e) => e.issue_type === t)})</option>)}
                  </optgroup>
                </select>
                <div className="muted small" style={{ marginTop: 6 }}>
                  {type ? `${shown.length} cases, oldest first.` : scope === "highlights" ? "One representative case for each kind of situation." : `${shown.length} cases, oldest first.`} Use ↑ ↓ to step through.
                </div>
              </div>
              {shown.length === 0 ? <div className="empty">No cases of this kind.</div> : (
                <div className="list">
                  {shown.map((e) => (
                    <button key={e.id} id={`inc-${e.id}`} className="item" aria-pressed={selected?.id === e.id} onClick={() => setSel(e.id)}>
                      <span className="bar" style={{ background: issue(e.issue_type).color }} />
                      <span><span className="title">{headline(e)}</span><span className="sub">{issue(e.issue_type).label} · {fmtTime(e.start, tz)}</span></span>
                      {decided(e) ? <span className="light done">Decided</span> : <Light autonomy={strictest(e)} short />}
                    </button>
                  ))}
                </div>
              )}
              <div className="keyline"><span><i className="swatch" style={{ background: "var(--good)" }} />Steward acted on its own</span><span><i className="swatch" style={{ background: "var(--warning)" }} />waits for a person</span></div>
            </div>
            {selected
              ? <IncidentStory key={selected.id} e={selected} tz={tz} outcome={data.scorecard?.matched?.[selected.id]} Technical={Technical} answers={answers} onAnswer={onAnswer}
                  locator={selected.rooms.length > 0 && <FloorMap rooms={data.rooms} highlight={selected.rooms} focus />} />
              : <div className="card empty">No incidents in this dataset.</div>}
          </div>
        </div>
      )}

      {view === "lessons" && <Lessons key={`${data.meta.profile}/${data.meta.start}/${data.meta.end}`} data={data} FloorMap={FloorMap} />}

      {view === "record" && <Record data={data} tz={tz} />}
    </div>
  );
}
