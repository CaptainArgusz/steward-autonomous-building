import React, { useEffect, useState } from "react";
import { BarChart } from "./charts.jsx";
import { cap, firstSentence } from "./plain.js";
async function loadRecommendations(profile, mode, signal) {
  const response = await fetch(`${import.meta.env.BASE_URL}api/recommendations`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile, mode }), signal,
  });
  if (!response.headers.get("Content-Type")?.includes("application/json")) {
    throw new Error("Open Steward through make ui to generate recommendations.");
  }
  const payload = await response.json();
  if (!response.ok || payload.error) throw new Error(payload.error || "The report could not be generated.");
  return payload;
}

// One request chain per dataset for the whole session: opening Lessons again reuses it, and the
// app starts it as soon as a dataset is loaded so the report is usually ready before the tab is opened.
const loaded = new Map(); // profile -> { evidence: Promise, report: Promise, done: resolved report payload }
export function preloadLessons(profile) {
  if (!loaded.has(profile)) {
    const entry = { done: null };
    entry.evidence = loadRecommendations(profile, "evidence");
    entry.report = entry.evidence.then(() => loadRecommendations(profile, "recommend"));
    entry.report.then((result) => { entry.done = result; }, () => loaded.delete(profile)); // a failure can be retried
    loaded.set(profile, entry);
  }
  return loaded.get(profile);
}

const KIND = {
  PLATO: { cls: "plato", badge: "PLATO · build", verb: "Build it differently", title: "Build differently", sub: "Design changes for the next PLATO building" },
  CLARA: { cls: "clara", badge: "CLARA · operate", verb: "Operate it differently", title: "Operate differently", sub: "Rule changes CLARA can make without rebuilding" },
};

/** Split a recommendation into a short headline and the detail after the first natural break. */
function split(text) {
  const m = /^(.{30,}?)(?::\s|\.\s|;\s|\s-\s)(.+)$/s.exec(text || "");
  return m ? [m[1], cap(m[2])] : [text, ""];
}

/** One lesson: what the year showed (map or chart) on the left, what to change on the right. */
function Lesson({ ins, rooms, FloorMap, observations }) {
  const k = KIND[ins.target] || KIND.CLARA;
  const spatial = ins.rooms && Object.keys(ins.rooms).length > 0;
  const charts = ins.charts || [];
  const [lead, rest] = split(ins.recommendation);
  const short = firstSentence(ins.finding);
  const pct = ins.confidence != null ? Math.round(ins.confidence * 100) : null;
  return (
    <article className={`card lesson ${k.cls}`}>
      <div className="visual">
        <div className="eyebrow">What the data showed</div>
        {spatial ? (
          <>
            <FloorMap rooms={rooms} heat={{ rooms: ins.rooms, label: "" }} />
            <div className="muted small">Each room: {ins.rooms_unit || "value per room"}. Darker is higher.</div>
          </>
        ) : charts[0] ? <BarChart chart={charts[0]} /> : <div className="muted small">{ins.comparison}</div>}
      </div>
      <div className="ltext">
        <div className="ihead">
          <span className={`badge ${k.cls}`}>{k.badge}</span>
          <span className="muted small">{ins.insight_id} · AI recommendation</span>
          <span className="spacer" />
          {pct != null && <span className="conf" title="Confidence in the computed observation; recommendation priority is advisory"><i><b style={{ width: `${pct}%` }} /></i>{pct} % confident</span>}
        </div>
        <h2>{ins.topic}</h2>
        <p className="finding">{short}</p>
        <div className="rec">
          <div className="eyebrow">{k.verb}</div>
          <p className="lead">{lead}</p>
          {rest && <p>{rest}</p>}
        </div>
        {ins.why_it_fits && <p><b>Why it matters.</b> {ins.why_it_fits}</p>}
        {ins.expected_benefit && <p className="gain"><b>What it gains.</b> {ins.expected_benefit}</p>}
        {ins.trade_off && <p className="gain"><b>Trade-off.</b> {ins.trade_off}</p>}
        {ins.next_check && <p className="gain"><b>Before committing.</b> {ins.next_check}</p>}
        <details className="more">
          <summary>How sure are we? Show the proof</summary>
          <dl className="kv">
            {ins.uncertainty && <><dt>Uncertainty</dt><dd>{ins.uncertainty}</dd></>}
            {short !== ins.finding && <><dt>Full finding</dt><dd>{ins.finding}</dd></>}
            {ins.comparison && <><dt>Compared</dt><dd>{ins.comparison}</dd></>}
            {ins.evidence && <><dt>Evidence</dt><dd><ul className="plain">{ins.evidence.map((x, i) => <li key={i}>{x}</li>)}</ul></dd></>}
            {ins.evidence_ids?.slice(1).map((id) => {
              const other = observations.find((o) => o.insight_id === id);
              return other && <React.Fragment key={id}><dt>{id} · {other.topic}</dt><dd>{other.finding}<p>{other.uncertainty}</p></dd></React.Fragment>;
            })}
          </dl>
          {(spatial ? charts : charts.slice(1)).map((c, i) => <BarChart key={i} chart={c} />)}
        </details>
      </div>
    </article>
  );
}

/** Recommendations keep the same evidence/map cards as the original Lessons page. */
export function Lessons({ data, FloorMap }) {
  const [show, setShow] = useState("all");
  const ready = loaded.get(data.meta.profile)?.done;
  const [evidence, setEvidence] = useState(ready?.evidence || data.learning || null);
  const [report, setReport] = useState(ready?.report || null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(!ready);

  useEffect(() => {
    let live = true;
    const entry = preloadLessons(data.meta.profile);
    setError("");
    setShow("all");
    setReport(entry.done?.report || null);
    setLoading(!entry.done);
    entry.evidence.then((observed) => { if (live && !entry.done) setEvidence(observed.evidence); }, () => {});
    entry.report
      .then((result) => { if (live) { setEvidence(result.evidence); setReport(result.report); } })
      .catch((e) => { if (live) setError(e.message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [data.meta.profile]);

  const observations = evidence?.observations || [];
  const checked = evidence?.checked || [];
  const lessons = (report?.recommendations || []).map((rec, index) => ({
    ...observations.find((item) => item.insight_id === rec.evidence_ids[0]),
    ...rec, insight_id: `AI-${index + 1}`,
  }));
  const of = (t) => lessons.filter((i) => i.target === t);
  const groups = ["PLATO", "CLARA"].filter((t) => of(t).length > 0 && (show === "all" || show === t));
  const tiles = [
    ["PLATO", of("PLATO").length, KIND.PLATO.title, KIND.PLATO.sub],
    ["CLARA", of("CLARA").length, KIND.CLARA.title, KIND.CLARA.sub],
    ["checked", checked.length, "Hypotheses checked", "Results and uncertainties from this dataset"],
  ];
  return (
    <div className="page">
      <div className="page-head">
        <h1>What the data teaches the next building</h1>
      </div>
      {loading && <p className="muted small" role="status">Preparing recommendations from the selected dataset…</p>}
      {error && <p className="lesson-error" role="alert">{error} Reload Lessons to try again.</p>}
      {report && <>
        <div className="lessontiles">
          {tiles.map(([key, n, title, sub]) => (
            <button key={key} className={`ltile ${key.toLowerCase()}`} aria-pressed={show === key} onClick={() => setShow(show === key ? "all" : key)}>
              <b>{n}</b><span><strong>{title}</strong>{sub}</span>
            </button>
          ))}
        </div>
        {groups.map((t) => (
          <section key={t} className="stackcol">
            <div className="section-title"><h2>{KIND[t].title}</h2><span className="muted small">{KIND[t].sub}</span></div>
            {of(t).map((ins) => <Lesson key={ins.insight_id} ins={ins} rooms={data.rooms} FloorMap={FloorMap} observations={observations} />)}
          </section>
        ))}
      </>}
      {!report && evidence && <section className="stackcol">
        <div className="section-title"><h2>Evidence from this dataset</h2><span className="muted small">{observations.length} observations available for recommendations</span></div>
        <div className="card checked">{observations.map((item) => <details key={item.insight_id}>
          <summary>{item.topic}</summary><p>{item.finding}</p><p className="muted small">{item.uncertainty}</p>
        </details>)}</div>
      </section>}
      {(show === "all" || show === "checked") && checked.length > 0 && (
        <section className="stackcol">
          <div className="section-title"><h2>Hypotheses checked</h2><span className="muted small">Findings from the selected dataset</span></div>
          <div className="card checked">
            {checked.map((c) => <details key={c.hypothesis}><summary>{c.hypothesis}</summary><p className="muted">{c.result}</p></details>)}
          </div>
        </section>
      )}
      {evidence?.skipped.length > 0 && <details className="card checked">
        <summary>Where the evidence is limited</summary>
        {evidence.skipped.map((item) => <p key={item.analysis}>{item.reason}</p>)}
      </details>}
    </div>
  );
}
