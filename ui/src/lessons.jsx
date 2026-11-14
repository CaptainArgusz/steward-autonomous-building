import React, { useState } from "react";
import { BarChart } from "./charts.jsx";
import { cap, firstSentence } from "./plain.js";
// The hypotheses that were tested and not supported live in insights.md only; read them at build time.
import insightsMd from "../../insights.md?raw";

/** "- **Title.** explanation" lines of the "Checked and not found" section. */
const CHECKED = (() => {
  const section = (insightsMd.split(/^## Checked and not found\s*$/m)[1] || "").split(/^## /m)[0];
  return [...section.matchAll(/^- \*\*(.+?)\*\*\s*(.+)$/gm)].map((m) => ({ title: m[1].replace(/\.$/, ""), text: m[2] }));
})();

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
function Lesson({ ins, rooms, FloorMap }) {
  const k = KIND[ins.target] || KIND.CLARA;
  const spatial = ins.rooms && Object.keys(ins.rooms).length > 0;
  const charts = ins.charts || [];
  const [lead, rest] = split(ins.recommendation);
  const short = firstSentence(ins.finding);
  const pct = ins.confidence != null ? Math.round(ins.confidence * 100) : null;
  return (
    <article className={`card lesson ${k.cls}`}>
      <div className="visual">
        <div className="eyebrow">What the year showed</div>
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
          <span className="muted small">{ins.insight_id}</span>
          <span className="spacer" />
          {pct != null && <span className="conf" title="Our confidence in the finding"><i><b style={{ width: `${pct}%` }} /></i>{pct} % confident</span>}
        </div>
        <h2>{ins.topic}</h2>
        <p className="finding">{short}</p>
        <div className="rec">
          <div className="eyebrow">{k.verb}</div>
          <p className="lead">{lead}</p>
          {rest && <p>{rest}</p>}
        </div>
        {ins.expected_benefit && <p className="gain"><b>What it gains.</b> {ins.expected_benefit}</p>}
        <details className="more">
          <summary>How sure are we? Show the proof</summary>
          <dl className="kv">
            {ins.uncertainty && <><dt>Uncertainty</dt><dd>{ins.uncertainty}</dd></>}
            {short !== ins.finding && <><dt>Full finding</dt><dd>{ins.finding}</dd></>}
            {ins.comparison && <><dt>Compared</dt><dd>{ins.comparison}</dd></>}
            {ins.evidence && <><dt>Evidence</dt><dd><ul className="plain">{ins.evidence.map((x, i) => <li key={i}>{x}</li>)}</ul></dd></>}
          </dl>
          {(spatial ? charts : charts.slice(1)).map((c, i) => <BarChart key={i} chart={c} />)}
        </details>
      </div>
    </article>
  );
}

/** Lessons: what to build and operate differently, each with its proof, plus what was tested and rejected. */
export function Lessons({ data, FloorMap }) {
  const [show, setShow] = useState("all");
  const of = (t) => data.insights.filter((i) => i.target === t);
  const groups = ["PLATO", "CLARA"].filter((t) => of(t).length > 0 && (show === "all" || show === t));
  const tiles = [
    ["PLATO", of("PLATO").length, KIND.PLATO.title, KIND.PLATO.sub],
    ["CLARA", of("CLARA").length, KIND.CLARA.title, KIND.CLARA.sub],
    ["checked", CHECKED.length, "Checked, not supported", "Ideas we tested that the data did not back"],
  ];
  return (
    <div className="page">
      <div className="page-head">
        <h1>What a year teaches the next building</h1>
        <p>Each lesson pairs what the data showed with one change, and says how sure we are. Computed from the full dev year of the fictional PLATO-40; no saving is measured.</p>
      </div>
      {data.insights.length === 0 ? <div className="card empty">No findings in this dataset. They are computed from the full dev year; switch the dataset at the top right.</div> : (
        <>
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
              {of(t).map((ins) => <Lesson key={ins.insight_id} ins={ins} rooms={data.rooms} FloorMap={FloorMap} />)}
            </section>
          ))}
          {(show === "all" || show === "checked") && CHECKED.length > 0 && (
            <section className="stackcol">
              <div className="section-title"><h2>Checked, not supported</h2><span className="muted small">Plausible ideas we tested on the same data and did not turn into a recommendation.</span></div>
              <div className="card checked">
                {CHECKED.map((c) => (
                  <details key={c.title}>
                    <summary><span className="no">✕</span>{c.title}</summary>
                    <p className="muted">{c.text}</p>
                  </details>
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
