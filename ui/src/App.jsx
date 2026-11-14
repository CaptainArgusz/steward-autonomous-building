import React, { useEffect, useMemo, useState } from "react";
import { BarChart } from "./charts.jsx";
import { STATUS, fmtTime, issue, rampColor } from "./util.js";
import { TOUR_ORDER, action, featured, friendly, headline, isIncident, name as nice, strictest } from "./plain.js";
import { IncidentStory, Light, Presentation } from "./story.jsx";
import { Manager } from "./manager.jsx";

const DATASETS = ["quickstart", "dev", "holdout"];

function Seg({ value, onChange, options }) {
  return (
    <div className="seg" role="group">
      {options.map(([v, label]) => (
        <button key={v} aria-pressed={value === v} onClick={() => onChange(v)}>{label}</button>
      ))}
    </div>
  );
}

/**
 * Building map: one band per floor, north rooms above the corridor, south rooms below.
 * A room tile shows its number, a total incident count and a thin stripe per incident type.
 * ``focus`` (presentation mode) greys out every room that is not part of the current case.
 */
export function FloorMap({ rooms, episodes, selectedRoom, onRoom, heat, highlight = [], focus = false }) {
  const byRoom = useMemo(() => {
    const m = {};
    for (const e of episodes) for (const r of e.rooms) ((m[r] ||= {})[e.issue_type] = (m[r][e.issue_type] || 0) + 1);
    return m;
  }, [episodes]);
  const floors = [...new Set(rooms.map((r) => r.floor))].sort((a, b) => b - a);
  const hv = heat ? Object.values(heat.rooms).filter((v) => v != null) : [];
  const lo = Math.min(...hv), hi = Math.max(...hv);
  const used = [...new Set(episodes.flatMap((e) => (e.rooms.length ? [e.issue_type] : [])))];

  const cell = (r) => {
    const [bg, fg] = heat ? rampColor(heat.rooms[r.id], lo, hi) : [null, null];
    const counts = byRoom[r.id] || {};
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    const hl = highlight.includes(r.id);
    const tip = [`Room ${r.id.slice(1)} · floor ${r.floor} · ${r.orientation === "N" ? "north" : "south"} side · ${r.area_m2} m²${r.has_bathtub ? " · bathtub" : ""}`,
      ...Object.entries(counts).map(([t, n]) => `${n} × ${issue(t).label}`)].join("\n");
    return (
      <button key={r.id} className={`room${hl ? " hl" : ""}${focus && !hl ? " dim" : ""}`} aria-pressed={selectedRoom === r.id}
        onClick={() => onRoom && onRoom(selectedRoom === r.id ? null : r.id)}
        style={bg ? { background: bg, color: fg, borderColor: bg } : undefined} title={tip}>
        <span className="rno">{r.id.slice(1)}</span>
        {heat ? <span className="val">{heat.rooms[r.id] != null ? heat.rooms[r.id] : "–"}</span>
          : !focus && total > 0 && (
            <span className="rdots">
              {TOUR_ORDER.filter((t) => counts[t]).map((t) => <i key={t} style={{ background: issue(t).color }}>{counts[t]}</i>)}
            </span>
          )}
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
      {heat ? (
        <div className="ramp"><span>{lo}</span><i /><span>{hi}</span><span>· {heat.label}</span></div>
      ) : !focus && (
        <div className="legend">
          {used.map((t) => <span key={t}><i className="swatch" style={{ background: issue(t).color }} />{issue(t).label}</span>)}
          <span className="muted" style={{ width: "100%" }}>Number in a dot = incidents of that kind in the room. Top row of each floor faces north.</span>
        </div>
      )}
    </div>
  );
}

/** Bucket key and label for a time: months for long datasets, days for short ones. */
function bucketer(meta, tz) {
  const days = (Date.parse(meta.end) - Date.parse(meta.start)) / 864e5;
  const daily = days <= 62;
  const parts = (t) => Object.fromEntries(new Intl.DateTimeFormat("en-GB", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(new Date(t)).map((p) => [p.type, p.value]));
  const key = (t) => { const p = parts(t); return daily ? `${p.year}-${p.month}-${p.day}` : `${p.year}-${p.month}`; };
  const keys = [];
  for (let t = Date.parse(meta.start); t < Date.parse(meta.end); t += daily ? 864e5 : 864e5 * 27) {
    const k = key(t);
    if (keys[keys.length - 1] !== k) keys.push(k);
  }
  const label = (k) => daily ? String(Number(k.slice(8))) : new Date(`${k}-15T12:00:00Z`).toLocaleDateString("en-GB", { month: "short" });
  const long = (k) => daily ? new Date(`${k}T12:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short" })
    : new Date(`${k}-15T12:00:00Z`).toLocaleDateString("en-GB", { month: "long", year: "numeric" });
  return { key, keys, label, long, daily };
}

/** Incidents per month (or day), stacked by type. Click a bar to filter the list to that period. */
function PeriodChart({ episodes, b, period, onPick }) {
  const by = {};
  for (const e of episodes) {
    const k = b.key(e.start);
    (by[k] ||= {})[e.issue_type] = (by[k][e.issue_type] || 0) + 1;
  }
  const total = (k) => Object.values(by[k] || {}).reduce((a, c) => a + c, 0);
  const max = Math.max(1, ...b.keys.map(total));
  const order = [...TOUR_ORDER, ...new Set(episodes.map((e) => e.issue_type).filter((t) => !TOUR_ORDER.includes(t)))];
  return (
    <div className={`pchart${period ? " picked" : ""}`}>
      {b.keys.map((k, i) => {
        const n = total(k);
        const tip = [`${b.long(k)}: ${n} incident${n === 1 ? "" : "s"}`, ...order.filter((t) => by[k]?.[t]).map((t) => `${by[k][t]} × ${issue(t).label}`)].join("\n");
        return (
          <button key={k} className="pcol" aria-pressed={period === k} title={tip} onClick={() => onPick(period === k ? null : k)}>
            <span className="pn">{n || ""}</span>
            <span className="pbar" style={{ height: `${(n / max) * 100}%` }}>
              {order.filter((t) => by[k]?.[t]).map((t) => <i key={t} style={{ flex: by[k][t], background: issue(t).color }} />)}
            </span>
            <span className="pl">{!b.daily || i % 3 === 0 ? b.label(k) : ""}</span>
          </button>
        );
      })}
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

function Insight({ ins, active, onMap }) {
  const spatial = ins.rooms && Object.keys(ins.rooms).length > 0;
  return (
    <div className="card">
      <div className="head" style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
        <span className="badge">{ins.target}</span>
        <h2 style={{ margin: 0 }}>{ins.topic}</h2>
        <span className="spacer" />
        {spatial && <button className="chip" aria-pressed={active} onClick={onMap}>{active ? "Showing on map" : "Show on map"}</button>}
      </div>
      <div>{ins.finding}</div>
      {(ins.charts || []).map((c, i) => <BarChart key={i} chart={c} />)}
      <dl className="kv">
        <dt>Recommend</dt><dd>{ins.recommendation}</dd>
        {ins.expected_benefit && <><dt>Benefit</dt><dd>{ins.expected_benefit}</dd></>}
        {ins.uncertainty && <><dt>Uncertainty</dt><dd>{ins.uncertainty}</dd></>}
        {ins.evidence && <><dt>Evidence</dt><dd><ul className="plain">{ins.evidence.map((x, i) => <li key={i}>{x}</li>)}</ul></dd></>}
        {ins.confidence != null && <><dt>Confidence</dt><dd>{Math.round(ins.confidence * 100)} %</dd></>}
      </dl>
    </div>
  );
}

export default function App() {
  const params = new URLSearchParams(location.search); // ?data=dev&tab=incidents&sel=<episode id>
  const [name, setName] = useState(params.get("data") || "dev");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [mode, setMode] = useState(params.get("mode") || "present");
  const [tab, setTab] = useState(params.get("tab") || "incidents");
  const [room, setRoom] = useState(null);
  const [type, setType] = useState(null);
  const [sel, setSel] = useState(params.get("sel"));
  const [period, setPeriod] = useState(null);
  const [mapInsight, setMapInsight] = useState(null);

  useEffect(() => {
    setData(null); setError(null); setRoom(null); setMapInsight(null); setPeriod(null);
    if (name !== (params.get("data") || "dev")) setSel(null);
    fetch(`./data/${name}.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`No bundle for "${name}". Run: uv run steward ui data/${name}`))))
      .then((d) => {
        setData(d);
        if (!d.insights.length) setTab((t) => (t === "findings" ? "incidents" : t));
        // optional second file with room states per day for the duty-manager replay
        fetch(`./data/${name}.days.json`)
          .then((r) => (r.ok ? r.json() : null))
          .then((x) => { if (x?.days) setData((cur) => (cur === d ? { ...d, days: x.days } : cur)); })
          .catch(() => {});
      })
      .catch((e) => setError(e.message));
  }, [name]);

  const header = (
    <header className="top">
      <div><h1>Steward · PLATO-40</h1><div className="sub">Autonomous building operations on a replayed year · synthetic reference building</div></div>
      <span className="spacer" />
      <Seg value={mode} onChange={setMode} options={[["present", "Presentation"], ["manager", "Duty manager"], ["explore", "Explore"]]} />
      <Seg value={name} onChange={setName} options={DATASETS.map((d) => [d, d])} />
    </header>
  );
  if (error) return <div className="app">{header}<main><div className="card empty">{error}</div></main></div>;
  if (!data) return <div className="app">{header}<main><div className="card empty">Loading…</div></main></div>;

  const tz = data.meta.local_timezone;
  const types = [...new Set(data.episodes.map((e) => e.issue_type))];
  const b = bucketer(data.meta, tz);
  const inRoom = (e) => !room || e.rooms.includes(room) || e.assets.includes(room);
  const ofType = (e) => (type ? e.issue_type === type : isIncident(e));  // guest replies only when chosen
  const shown = data.episodes.filter((e) => ofType(e) && inRoom(e) && (!period || b.key(e.start) === period));
  const incidents = data.episodes.filter(isIncident);
  const selected = data.episodes.find((e) => e.id === sel) || null;
  const insight = mapInsight != null ? data.insights.find((i) => i.insight_id === mapInsight) : null;
  const heat = tab === "findings" && insight ? { rooms: insight.rooms, label: `${insight.rooms_unit || ""} · ${insight.insight_id} ${insight.topic}` } : null;
  const plant = [...new Set(data.episodes.flatMap((e) => e.assets.filter((a) => !e.rooms.includes(a))))];
  const count = (pred) => data.episodes.filter(pred).length;
  const pick = (id) => { setSel(id); setTab("incidents"); setMode("explore"); };
  const cases = featured(data.episodes, data.scorecard?.matched || {});
  const highlight = selected && tab === "incidents" ? selected.rooms : [];

  if (mode === "manager")
    return (
      <div className="app">
        {header}
        <Manager data={data} tz={tz} onOpen={pick} />
      </div>
    );

  if (mode === "present")
    return (
      <div className="app">
        {header}
        <Presentation data={data} cases={cases} tz={tz} onExplore={pick}
          Map={({ highlight: h }) => <FloorMap rooms={data.rooms} episodes={incidents} highlight={h} focus />} />
      </div>
    );
  const decisions = data.episodes.flatMap((e) => e.decisions);
  const nAuto = (a) => decisions.filter((d) => d.autonomy === a).length;

  return (
    <div className="app">
      {header}
      <main>
        <div className="sticky">
          <div className="card">
            <h2>Building{room ? ` · ${nice(room)} selected` : ""}</h2>
            <FloorMap rooms={data.rooms} episodes={data.episodes.filter(ofType)} selectedRoom={room} onRoom={(r) => { setRoom(r); if (r) setTab("incidents"); }} heat={heat} highlight={highlight} />
            {plant.length > 0 && !heat && (
              <details className="plant-wrap" open={plant.includes(room)}>
                <summary>Equipment, staff & logistics ({plant.length})</summary>
                <div className="plant">
                {plant.map((a) => (
                  <button key={a} className="chip" title={a} aria-pressed={room === a} onClick={() => { setRoom(room === a ? null : a); setTab("incidents"); }}>
                    {nice(a)} <b>{count((e) => e.assets.includes(a))}</b>
                  </button>
                ))}
                </div>
              </details>
            )}
          </div>
          <div className="card">
            <h2>Incidents {b.daily ? "per day" : "per month"}{room || type ? " · filtered" : ""}</h2>
            <PeriodChart episodes={data.episodes.filter((e) => ofType(e) && inRoom(e))} b={b} period={period}
              onPick={(k) => { setPeriod(k); setTab("incidents"); }} />
            <div className="legend">
              {TOUR_ORDER.filter((t) => data.episodes.some((e) => e.issue_type === t && ofType(e))).map((t) => <span key={t}><i className="swatch" style={{ background: issue(t).color }} />{issue(t).label}</span>)}
              <span className="muted" style={{ width: "100%" }}>Click a bar to list only that {b.daily ? "day" : "month"}; hover for the breakdown.</span>
            </div>
            <div className="scn" style={{ marginTop: 12 }}>
              <div className="tile"><div className="n">{incidents.length}</div><div className="muted small">incidents</div></div>
              <div className="tile go"><div className="n">{nAuto("autonomous")}</div><div className="muted small">done automatically</div></div>
              <div className="tile wait"><div className="n">{nAuto("human_approval") + nAuto("approval_and_consent")}</div><div className="muted small">sent to a human</div></div>
              <div className="tile"><div className="n">{data.dismissed.length}</div><div className="muted small">look-alikes ignored</div></div>
            </div>
            <div className="muted small" style={{ marginTop: 8 }}>{data.meta.note}</div>
          </div>
        </div>

        <div>
          <div style={{ marginBottom: 12 }}>
            <Seg value={tab} onChange={setTab} options={[["incidents", `Incidents (${incidents.length})`], ["dismissed", `False alarms avoided (${data.dismissed.length})`], ["findings", `Lessons for the next building (${data.insights.length})`], ["score", "Scorecard"]]} />
          </div>

          {tab === "findings" && (data.insights.length === 0
            ? <div className="card empty">No findings in this bundle yet. They are computed from the dev year: <code>uv run python -m steward.insights data/dev</code>.</div>
            : data.insights.map((ins) => (
              <Insight key={ins.insight_id} ins={ins} active={mapInsight === ins.insight_id}
                onMap={() => setMapInsight(mapInsight === ins.insight_id ? null : ins.insight_id)} />
            )))}

          {tab === "incidents" && (
            <>
              <div className="card">
                <div className="filters">
                  <button className="chip" aria-pressed={!type} onClick={() => setType(null)}>All incidents</button>
                  {types.map((t) => (
                    <button key={t} className="chip" aria-pressed={type === t} onClick={() => setType(type === t ? null : t)}>
                      <i className="swatch" style={{ background: issue(t).color, margin: 0 }} />{issue(t).label} <b>{count((e) => e.issue_type === t)}</b>
                    </button>
                  ))}
                  {room && <button className="chip" aria-pressed="true" onClick={() => setRoom(null)}>{nice(room)} ✕</button>}
                  {period && <button className="chip" aria-pressed="true" onClick={() => setPeriod(null)}>{b.long(period)} ✕</button>}
                </div>
                {shown.length === 0 ? <div className="empty">No incidents match. {room ? "Nothing was raised for this room." : ""}</div> : (
                  <div className="list">
                    {shown.map((e) => (
                      <button key={e.id} className="item" aria-pressed={sel === e.id} onClick={() => setSel(e.id)}>
                        <span className="bar" style={{ background: issue(e.issue_type).color }} />
                        <span><span className="title">{issue(e.issue_type).label}</span> <span className="muted">· {headline(e)}</span>
                          <br /><span className="muted small">{fmtTime(e.start, tz)} · {e.decisions.map((d) => `${action(d.action).icon} ${action(d.action).label}`).join(" → ")}</span></span>
                        <span className="stack"><Light autonomy={strictest(e)} short /><span className="muted small">{e.priority}</span></span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {selected ? <IncidentStory e={selected} tz={tz} outcome={data.scorecard?.matched?.[selected.id]} Technical={Technical} /> : <div className="card empty">Select an incident to see what happened, what Steward noticed, what it decided, who acts and how success is checked.</div>}
            </>
          )}

          {tab === "score" && (
            <div className="card">
              <h2>How we did against what the replay recorded</h2>
              {!data.scorecard ? <div className="empty">No scorecard in this bundle.</div> : (
                <>
                  <table className="tbl">
                    <thead><tr><th>Recorded outcome</th><th>Recorded</th><th>We flagged it before</th><th>Median lead</th></tr></thead>
                    <tbody>
                      {data.scorecard.outcomes.map((r) => (
                        <tr key={r.key}>
                          <td><i className="swatch" style={{ background: issue(r.issue_type).color }} />{r.label}</td>
                          <td>{r.recorded}</td><td>{r.recorded ? `${r.flagged_before} of ${r.recorded}` : "–"}</td>
                          <td>{r.median_lead_h != null ? `${r.median_lead_h} h` : "–"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <table className="tbl" style={{ marginTop: 14 }}>
                    <thead><tr><th>Our incidents ({data.scorecard.weeks} weeks)</th><th>Incidents</th><th>Per week</th><th>Matched a recorded outcome</th></tr></thead>
                    <tbody>
                      {data.scorecard.by_issue.map((r) => (
                        <tr key={r.issue_type}>
                          <td><i className="swatch" style={{ background: issue(r.issue_type).color }} />{issue(r.issue_type).label}</td>
                          <td>{r.incidents}</td><td>{r.incidents_per_week}</td><td>{r.incidents_with_recorded_outcome}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="muted small">{data.scorecard.caveat}</p>
                </>
              )}
            </div>
          )}

          {tab === "dismissed" && (
            <div className="card">
              <h2>Things that looked like a problem, and why Steward did not raise an alarm</h2>
              <div className="muted small" style={{ marginBottom: 8 }}>A quiet system is a trustworthy one: each of these was checked and dismissed with a reason.</div>
              {data.dismissed.length === 0 ? <div className="empty">Nothing recorded.</div> : (
                <div className="list" style={{ maxHeight: 640 }}>
                  {data.dismissed.filter((d) => !room || d.asset === room).map((d, i) => (
                    <div key={i} className="item" style={{ cursor: "default" }}>
                      <span className="bar" style={{ background: issue(d.issue_type).color }} />
                      <span><span className="title">{nice(d.asset)}</span> <span className="muted">· {issue(d.issue_type).label} · {fmtTime(d.timestamp_utc, tz)}</span><br />{friendly(d.reason, tz)}</span>
                      <span />
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
