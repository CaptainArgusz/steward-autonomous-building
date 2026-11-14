import React, { useEffect, useMemo, useState } from "react";
import { BarChart, LineChart } from "./charts.jsx";
import { AUTONOMY, STATUS, fmtDay, fmtTime, issue, rampColor } from "./util.js";

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

function FloorMap({ rooms, episodes, selectedRoom, onRoom, heat }) {
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
    return (
      <button key={r.id} className="room" aria-pressed={selectedRoom === r.id} onClick={() => onRoom(selectedRoom === r.id ? null : r.id)}
        style={bg ? { background: bg, color: fg, borderColor: bg } : undefined}
        title={`${r.id} · floor ${r.floor} · ${r.orientation === "N" ? "north" : "south"} · ${r.area_m2} m²${r.has_bathtub ? " · bathtub" : ""}`}>
        <div className="rid">{r.id}{r.has_bathtub ? " ◡" : ""}</div>
        {heat ? (
          <div className="val">{heat.rooms[r.id] != null ? heat.rooms[r.id] : "–"}</div>
        ) : (
          <div className="dots">
            {Object.entries(counts).map(([t, n]) => <span key={t} className={`dot ${issue(t).cls}`} title={`${n} × ${issue(t).label}`}>{n}</span>)}
          </div>
        )}
      </button>
    );
  };

  return (
    <div>
      {floors.map((f) => {
        const on = rooms.filter((r) => r.floor === f).sort((a, b) => a.id.localeCompare(b.id));
        return (
          <div className="floor" key={f}>
            <div className="floor-label">Floor {f}</div>
            <div className="rows">
              <div className="row"><span className="side">N</span>{on.filter((r) => r.orientation === "N").map(cell)}</div>
              <div className="corridor" />
              <div className="row"><span className="side">S</span>{on.filter((r) => r.orientation === "S").map(cell)}</div>
            </div>
          </div>
        );
      })}
      {heat ? (
        <div className="ramp"><span>{lo}</span><i /><span>{hi}</span><span>· {heat.label}</span></div>
      ) : (
        <div className="legend">
          {["room_turnaround", "water_leak", "heating_comfort", "ventilation_maintenance", "occupancy_surge", "logistics", "energy_optimization"].map((t) => (
            <span key={t}><i className="swatch" style={{ background: issue(t).color }} />{issue(t).label}</span>
          ))}
          <span>number = incidents in that room · ◡ bathtub</span>
        </div>
      )}
    </div>
  );
}

function YearStrip({ meta, episodes, selected, onSelect }) {
  const t0 = Date.parse(meta.start), t1 = Date.parse(meta.end);
  const months = [];
  for (let d = new Date(t0); d < t1; d.setUTCMonth(d.getUTCMonth() + 1)) months.push(d.toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" }));
  return (
    <div>
      <div className="strip">
        {episodes.map((e) => (
          <button key={e.id} className={`tick ${issue(e.issue_type).cls}`} aria-pressed={selected === e.id} title={`${fmtDay(e.start)} · ${issue(e.issue_type).label}`}
            onClick={() => onSelect(e.id)}
            style={{ left: `${((Date.parse(e.start) - t0) / (t1 - t0)) * 100}%`, height: { critical: 32, high: 24, medium: 16, low: 10 }[e.priority] }} />
        ))}
      </div>
      {months.length > 1 && months.length < 14 ? <div className="months">{months.map((m, i) => <span key={i}>{m}</span>)}</div>
        : <div className="months"><span>{fmtDay(meta.start)}</span><span>{fmtDay(meta.end)}</span></div>}
    </div>
  );
}

function Decision({ d, tz }) {
  return (
    <div className="decision">
      <div className="head">
        <code>{d.action}</code>
        <span className={`badge p-${d.priority}`}>{d.priority}</span>
        <span className={`badge a-${d.autonomy}`}>{AUTONOMY[d.autonomy]}</span>
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

function EpisodeDetail({ e, tz }) {
  const marks = e.decisions.map((d) => d.timestamp_utc);
  return (
    <div className="card">
      <h2><i className="swatch" style={{ background: issue(e.issue_type).color }} />{issue(e.issue_type).label} · {e.assets.join(", ")}</h2>
      <div className="muted small">{fmtTime(e.start, tz)}{e.end !== e.start ? ` → ${fmtTime(e.end, tz)}` : ""} · {e.decisions.length} decision{e.decisions.length > 1 ? "s" : ""} · {e.id}</div>
      {e.decisions.map((d) => <Decision key={d.event_id} d={d} tz={tz} />)}
      {e.charts.map((c, i) => <LineChart key={i} chart={c} markers={marks} tz={tz} />)}
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
  const [tab, setTab] = useState(params.get("tab") || "findings");
  const [room, setRoom] = useState(null);
  const [type, setType] = useState(null);
  const [sel, setSel] = useState(params.get("sel"));
  const [mapInsight, setMapInsight] = useState(null);

  useEffect(() => {
    setData(null); setError(null); setRoom(null); setMapInsight(null);
    if (name !== (params.get("data") || "dev")) setSel(null);
    fetch(`./data/${name}.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`No bundle for "${name}". Run: uv run steward ui data/${name}`))))
      .then((d) => { setData(d); if (!d.insights.length) setTab((t) => (t === "findings" ? "incidents" : t)); })
      .catch((e) => setError(e.message));
  }, [name]);

  const header = (
    <header className="top">
      <div><h1>Steward · PLATO-40</h1><div className="sub">Autonomous building operations on a replayed year · synthetic reference building</div></div>
      <span className="spacer" />
      <Seg value={name} onChange={setName} options={DATASETS.map((d) => [d, d])} />
    </header>
  );
  if (error) return <div className="app">{header}<main><div className="card empty">{error}</div></main></div>;
  if (!data) return <div className="app">{header}<main><div className="card empty">Loading…</div></main></div>;

  const tz = data.meta.local_timezone;
  const types = [...new Set(data.episodes.map((e) => e.issue_type))];
  const shown = data.episodes.filter((e) => (!type || e.issue_type === type) && (!room || e.rooms.includes(room) || e.assets.includes(room)));
  const selected = data.episodes.find((e) => e.id === sel) || null;
  const insight = mapInsight != null ? data.insights.find((i) => i.insight_id === mapInsight) : null;
  const heat = tab === "findings" && insight ? { rooms: insight.rooms, label: `${insight.rooms_unit || ""} · ${insight.insight_id} ${insight.topic}` } : null;
  const plant = [...new Set(data.episodes.flatMap((e) => e.assets.filter((a) => !e.rooms.includes(a))))];
  const count = (pred) => data.episodes.filter(pred).length;
  const pick = (id) => { setSel(id); setTab("incidents"); };

  return (
    <div className="app">
      {header}
      <main>
        <div className="sticky">
          <div className="card">
            <h2>Building{room ? ` · ${room} selected` : ""}</h2>
            <FloorMap rooms={data.rooms} episodes={data.episodes} selectedRoom={room} onRoom={(r) => { setRoom(r); if (r) setTab("incidents"); }} heat={heat} />
            {plant.length > 0 && !heat && (
              <div className="plant">
                <span className="muted small" style={{ alignSelf: "center" }}>Plant & logistics</span>
                {plant.map((a) => (
                  <button key={a} className="chip" aria-pressed={room === a} onClick={() => { setRoom(room === a ? null : a); setTab("incidents"); }}>
                    {a} <b>{count((e) => e.assets.includes(a))}</b>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="card">
            <h2>Incidents over the period</h2>
            <YearStrip meta={data.meta} episodes={data.episodes} selected={sel} onSelect={pick} />
            <div className="scn" style={{ marginTop: 12 }}>
              <div className="tile"><div className="n">{data.episodes.length}</div><div className="muted small">incidents</div></div>
              <div className="tile"><div className="n">{data.meta.decisions}</div><div className="muted small">decisions</div></div>
              <div className="tile"><div className="n">{count((e) => e.needs_approval)}</div><div className="muted small">need a human</div></div>
              <div className="tile"><div className="n">{data.dismissed.length}</div><div className="muted small">look-alikes dismissed</div></div>
            </div>
            <div className="muted small" style={{ marginTop: 8 }}>{data.meta.note}</div>
          </div>
        </div>

        <div>
          <div style={{ marginBottom: 12 }}>
            <Seg value={tab} onChange={setTab} options={[["findings", `Design findings (${data.insights.length})`], ["incidents", `Incidents (${data.episodes.length})`], ["dismissed", `Dismissed (${data.dismissed.length})`], ["score", "Scorecard"]]} />
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
                  <button className="chip" aria-pressed={!type} onClick={() => setType(null)}>All types</button>
                  {types.map((t) => (
                    <button key={t} className="chip" aria-pressed={type === t} onClick={() => setType(type === t ? null : t)}>
                      <i className="swatch" style={{ background: issue(t).color, margin: 0 }} />{issue(t).label} <b>{count((e) => e.issue_type === t)}</b>
                    </button>
                  ))}
                  {room && <button className="chip" aria-pressed="true" onClick={() => setRoom(null)}>{room} ✕</button>}
                </div>
                {shown.length === 0 ? <div className="empty">No incidents match. {room ? "Nothing was raised for this room." : ""}</div> : (
                  <div className="list">
                    {shown.map((e) => (
                      <button key={e.id} className="item" aria-pressed={sel === e.id} onClick={() => setSel(e.id)}>
                        <span className="bar" style={{ background: issue(e.issue_type).color }} />
                        <span><span className="title">{issue(e.issue_type).label}</span> <span className="muted">· {e.assets.slice(0, 4).join(", ")}{e.assets.length > 4 ? ` +${e.assets.length - 4}` : ""}</span>
                          <br /><span className="muted small">{fmtTime(e.start, tz)} · {e.decisions.map((d) => d.action).join(" → ")}</span></span>
                        <span className={`badge p-${e.priority}`}>{e.priority}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {selected ? <EpisodeDetail e={selected} tz={tz} /> : <div className="card empty">Select an incident to see its evidence, decisions, approval level and verification plan.</div>}
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
              <h2>Look-alikes the system examined and did not act on</h2>
              {data.dismissed.length === 0 ? <div className="empty">Nothing recorded.</div> : (
                <div className="list" style={{ maxHeight: 640 }}>
                  {data.dismissed.filter((d) => !room || d.asset === room).map((d, i) => (
                    <div key={i} className="item" style={{ cursor: "default" }}>
                      <span className="bar" style={{ background: issue(d.issue_type).color }} />
                      <span><span className="title">{d.asset}</span> <span className="muted">· {issue(d.issue_type).label} · {fmtTime(d.timestamp_utc, tz)}</span><br />{d.reason}</span>
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
