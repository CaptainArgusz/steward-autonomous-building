import React from "react";
import { issue } from "./util.js";
import { AGENTS, TOUR_ORDER, isIncident } from "./plain.js";

const LOOP = ["Observe", "Understand", "Decide & act", "Verify", "Learn & feed back"];

const span = (meta) => {
  const days = Math.round((Date.parse(meta.end) - Date.parse(meta.start)) / 864e5);
  return days >= 360 ? "One year" : `${days} days`;
};

/** Opening view: the whole loop in three beats, every number computed from the loaded bundle. */
export function Overview({ data, go }) {
  const incidents = data.episodes.filter(isIncident);
  const decisions = data.episodes.flatMap((e) => e.decisions);
  const n = (pred) => decisions.filter(pred).length;
  const auto = n((d) => d.autonomy === "autonomous");
  const approval = n((d) => d.autonomy === "human_approval");
  const consent = n((d) => d.autonomy === "approval_and_consent");
  const refused = n((d) => d.status === "refused_safety_check");
  const blocked = n((d) => d.status === "blocked_by_dependency");

  const outcomes = data.scorecard?.outcomes || [];
  const recorded = outcomes.reduce((a, o) => a + o.recorded, 0);
  const before = outcomes.reduce((a, o) => a + o.flagged_before, 0);

  const insights = data.insights || [];
  const plato = insights.filter((i) => i.target === "PLATO");
  const clara = insights.filter((i) => i.target === "CLARA");
  const top = (xs) => [...xs].sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0))[0];
  const quotes = [top(plato), top(clara)].filter(Boolean);

  const types = [...TOUR_ORDER, ...new Set(data.episodes.map((e) => e.issue_type).filter((t) => !TOUR_ORDER.includes(t)))]
    .map((t) => [t, data.episodes.filter((e) => e.issue_type === t).length]).filter(([, c]) => c > 0);

  return (
    <div className="page">
      <section className="hero">
        <div className="eyebrow">PLATO-40 · synthetic reference building · simulated replay</div>
        <h1>{span(data.meta)} of PLATO-40, run by Steward.</h1>
        <p>Steward watches every sensor, booking and roster change. It acts on its own where that is safe, asks a person where it is not, and turns the year into lessons for the next building.</p>
        <div className="loop">
          {LOOP.map((s, i) => <React.Fragment key={s}>{i > 0 && <i>→</i>}<span>{s}</span></React.Fragment>)}
        </div>
      </section>

      <div className="beats">
        <button className="beat" onClick={() => go("incidents")}>
          <div className="eyebrow">1 · Detect</div>
          <h2>Sees problems early</h2>
          <div className="big">{incidents.length}<small>incidents raised</small></div>
          <ul>
            {recorded > 0 && <li><b>{before}</b><span>of {recorded} problems the replay later recorded were flagged beforehand</span></li>}
            <li><b>{data.dismissed.length}</b><span>look-alikes checked and dismissed with a reason, not raised as alarms</span></li>
          </ul>
          <div className="cta">See the incidents →</div>
        </button>

        <button className="beat" onClick={() => go("day")}>
          <div className="eyebrow">2 · Decide & act</div>
          <h2>Acts safely, asks when it should</h2>
          <div className="big">{auto}<small>of {decisions.length} actions done automatically</small></div>
          <div className="splitbar" aria-hidden="true">
            {auto > 0 && <i className="go" style={{ flex: auto }} />}
            {approval > 0 && <i className="wait" style={{ flex: approval }} />}
            {consent > 0 && <i className="stop" style={{ flex: consent }} />}
          </div>
          <ul>
            <li><b>{approval}</b><span>waited for a person to approve</span></li>
            <li><b>{consent}</b><span>needed approval and the guest's consent</span></li>
            {refused + blocked > 0 && <li><b>{refused + blocked}</b><span>held back by the executor's safety and dependency checks</span></li>}
          </ul>
          <div className="cta">Watch a day in the building →</div>
        </button>

        <button className="beat" onClick={() => go(insights.length ? "lessons" : "record")}>
          <div className="eyebrow">3 · Learn & feed back</div>
          <h2>Teaches the next building</h2>
          {insights.length > 0 ? (
            <>
              <div className="big">{insights.length}<small>findings backed by the data</small></div>
              <ul>
                <li><b>{plato.length}</b><span>for PLATO: how to build the next one</span></li>
                <li><b>{clara.length}</b><span>for CLARA: how to operate it</span></li>
              </ul>
              {quotes.map((q) => <div key={q.insight_id} className="quote">{q.topic}</div>)}
              <div className="cta">Read the lessons →</div>
            </>
          ) : (
            <>
              <p className="muted">Findings are computed from the full year. Switch to the dev dataset to see them.</p>
              <div className="cta">See the track record →</div>
            </>
          )}
        </button>
      </div>

      <div className="section-title"><h2>What Steward handled</h2><span className="muted small">Pick a kind of situation to see real cases.</span></div>
      <div className="types">
        {types.map(([t, c]) => (
          <button key={t} className="typetile" onClick={() => go("incidents", { type: t })}>
            <span className="ic" style={{ background: `color-mix(in srgb, ${issue(t).color} 16%, white)` }}>{(AGENTS[t] || { icon: "•" }).icon}</span>
            <b>{c}</b>
            <span>{issue(t).label}</span>
          </button>
        ))}
      </div>

      <div className="footnote">
        {data.meta.note} {data.meta.decisions} decisions, {data.meta.validator_errors} contract errors. Findings concern the fictional PLATO-40 only.
      </div>
    </div>
  );
}
