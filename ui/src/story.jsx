import React, { useEffect, useState } from "react";
import { LineChart } from "./charts.jsx";
import { fmtTime, issue } from "./util.js";
import { action, agentFor, cap, evidence, firstSentence, friendly, headline, isIncident, light, name, problem } from "./plain.js";

const uniq = (xs) => [...new Set(xs.filter(Boolean))];

export function Light({ autonomy, short = false }) {
  const l = light(autonomy);
  return <span className={`light ${l.cls}`} title={l.note}>{short ? l.short : l.label}</span>;
}

export const STEPS = [
  "What happened",
  "What Steward noticed",
  "What it decided",
  "Who acts, who approves",
  "How we know it worked",
];

/** Everything the five steps need, derived from the incident's decisions. */
function parts(e, tz) {
  const decisions = [...e.decisions].sort((a, b) => (a.timestamp_utc < b.timestamp_utc ? -1 : 1));
  const p = problem(decisions);
  // split on sentence ends only (a full stop followed by a space and a capital), not on "2.0 h"
  const sentences = cap(friendly(p.reason, tz)).split(/(?<=[.!?])\s+(?=[A-Z])/);
  // the same action with the same guest text is one step in the story, not several
  const steps = [];
  for (const d of decisions) {
    const key = `${d.action}|${d.proposed_message || ""}|${d.resource_id || ""}`;
    const prev = steps.find((s) => s.key === key);
    if (prev) prev.n += 1;
    else steps.push({ key, d, n: 1 });
  }
  const groups = ["autonomous", "human_approval", "approval_and_consent"]
    .map((a) => [a, uniq(decisions.filter((d) => d.autonomy === a).map((d) => `${action(d.action).icon} ${action(d.action).label} → ${d.resource_id ? name(d.resource_id) : action(d.action).who}`))])
    .filter(([, xs]) => xs.length);
  return {
    decisions,
    what: sentences.slice(0, 2).join(" ").trim(),
    ev: uniq(decisions.flatMap((d) => d.evidence || [])).map((x) => evidence(x, tz)),
    steps,
    groups,
    verify: uniq(decisions.map((d) => cap(friendly(d.verification_plan, tz)))),
    expected: uniq(decisions.map((d) => d.expected_outcome && cap(friendly(d.expected_outcome.replace(/^Simulated:\s*/i, ""), tz)))),
  };
}

function More({ items, limit, render }) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, limit);
  return (
    <>
      {shown.map(render)}
      {items.length > limit && (
        <button className="link" onClick={() => setAll(!all)}>{all ? "Show less" : `Show ${items.length - limit} more`}</button>
      )}
    </>
  );
}

const chartTitle = (t, tz) => cap(friendly((t || "").replace(/\bfill_pct\b/, "fill level"), tz));

/** Content of one step. Short by default; details behind "Show more". */
export function StepBody({ k, e, tz, outcome, big = false }) {
  const p = parts(e, tz);
  const marks = p.decisions.map((d) => d.timestamp_utc);
  if (k === 0)
    return (
      <div>
        <p className={big ? "lead" : ""}>{p.what}</p>
        {e.charts.filter((c, i) => i === 0 || c.with_first).map((c, i) => <LineChart key={i} chart={{ ...c, title: chartTitle(c.title, tz) }} markers={marks} tz={tz} />)}
        {outcome && (
          <div className="callout"><b>Later the data recorded:</b> {outcome}. Steward had already acted before that.</div>
        )}
      </div>
    );
  if (k === 1)
    return (
      <ul className="evidence">
        <More items={p.ev} limit={3} render={(x, i) => <li key={i}><span className="src">{x.source}</span><span>{x.text}</span></li>} />
      </ul>
    );
  if (k === 2)
    return (
      <ol className="timeline">
        <More items={p.steps} limit={big ? 4 : 3} render={({ d, n }, i) => {
          const a = action(d.action);
          return (
            <li key={d.event_id}>
              <span className="when">{fmtTime(d.timestamp_utc, tz)}</span>
              <span className="icon">{a.icon}</span>
              <div>
                <span className="agent-tag">{agentFor(e.issue_type, d.action).icon} {agentFor(e.issue_type, d.action).name}</span>
                <b>{a.label}</b>{d.resource_id ? <span className="muted"> · {name(d.resource_id)}</span> : null}
                {n > 1 && <span className="muted small"> · {n}×</span>}
                <div className="muted">{cap(firstSentence(friendly(d.reason, tz)))}</div>
                {d.proposed_message && i === p.steps.findIndex((s) => s.d.proposed_message) && (
                  <div className="bubble-wrap">
                    <div className="bubble">{d.proposed_message}</div>
                    <div className="muted small">{d.message_language ? `${d.message_language.toUpperCase()} · ` : ""}proposed text, not sent</div>
                  </div>
                )}
              </div>
            </li>
          );
        }} />
      </ol>
    );
  if (k === 3)
    return (
      <div className="groups">
        {p.groups.map(([a, xs]) => (
          <div key={a} className={`group ${light(a).cls}`}>
            <Light autonomy={a} />
            <div className="muted small">{light(a).note}</div>
            <ul className="plain">{xs.map((x, i) => <li key={i}>{x}</li>)}</ul>
          </div>
        ))}
      </div>
    );
  return (
    <div>
      <ul className="plain checks">
        <More items={p.verify} limit={2} render={(v, i) => <li key={i}>{v}</li>} />
      </ul>
      {p.expected.length > 0 && (
        <div className="sim"><b>Expected effect (simulated):</b> {p.expected[0]}</div>
      )}
    </div>
  );
}

/** Explore view: all five steps of one incident, compact. */
export function IncidentStory({ e, tz, outcome, Technical }) {
  const [tech, setTech] = useState(false);
  return (
    <div className="card story">
      <div className="story-head">
        <span className="type-pill" style={{ background: issue(e.issue_type).color }}>{issue(e.issue_type).label}</span>
        <span className="muted small">{e.priority} priority · {fmtTime(e.start, tz)}{e.end !== e.start ? ` → ${fmtTime(e.end, tz)}` : ""}</span>
      </div>
      <h2 className="story-title">{headline(e)}</h2>
      {STEPS.map((t, k) => (
        <section className="step" key={k}>
          <div className="num">{k + 1}</div>
          <div className="body"><h3>{t}</h3><StepBody k={k} e={e} tz={tz} outcome={outcome} /></div>
        </section>
      ))}
      {Technical && (
        <div className="tech">
          <button className="link" onClick={() => setTech(!tech)}>{tech ? "Hide technical details" : "Show technical details (raw decisions, events)"}</button>
          {tech && <Technical e={e} tz={tz} />}
        </div>
      )}
    </div>
  );
}

const span = (meta) => {
  const days = Math.round((Date.parse(meta.end) - Date.parse(meta.start)) / 864e5);
  return days >= 360 ? "One year" : `${days} days`;
};

/** Presentation mode: full width, one case and one step at a time. */
export function Presentation({ data, cases, tz, Map, onExplore }) {
  const [idx, setIdx] = useState(0);
  const [step, setStep] = useState(0);
  useEffect(() => { setIdx(0); setStep(0); }, [data.meta.profile]);
  const e = cases[Math.min(idx, cases.length - 1)];
  const go = (i, s = 0) => { setIdx(i); setStep(s); };
  const next = () => (step < STEPS.length - 1 ? setStep(step + 1) : idx < cases.length - 1 && go(idx + 1));
  const back = () => (step > 0 ? setStep(step - 1) : idx > 0 && go(idx - 1, STEPS.length - 1));
  useEffect(() => {
    const onKey = (ev) => {
      if (ev.target.closest && ev.target.closest("input, textarea")) return;
      if (ev.key === "ArrowRight") next();
      if (ev.key === "ArrowLeft") back();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const all = data.episodes.flatMap((x) => x.decisions);
  const auto = all.filter((d) => d.autonomy === "autonomous").length;
  const human = all.length - auto;
  if (!e) return <div className="present"><div className="card empty">No incidents in this dataset.</div></div>;
  const outcome = data.scorecard?.matched?.[e.id];
  const equipment = e.assets.filter((a) => !e.rooms.includes(a));

  return (
    <div className="present">
      <div className="intro">
        <div>
          <h2>{span(data.meta)} of PLATO-40, run by Steward</h2>
          <div className="muted">A replay of a synthetic 40-room hotel. Every action is simulated: nothing was switched and no message was sent.</div>
        </div>
        <div className="nums">
          <div><b>{data.episodes.filter(isIncident).length}</b><span>incidents</span></div>
          <div className="go"><b>{auto}</b><span>done automatically</span></div>
          <div className="wait"><b>{human}</b><span>sent to a human</span></div>
          <div><b>{data.dismissed.length}</b><span>false alarms avoided</span></div>
        </div>
      </div>

      <div className="cases">
        {cases.map((c, i) => (
          <button key={c.id} className="case-tab" aria-pressed={i === idx} onClick={() => go(i)}>
            <i style={{ background: issue(c.issue_type).color }} />{issue(c.issue_type).label}
          </button>
        ))}
      </div>

      <div className="card stage">
        <aside>
          <span className="type-pill" style={{ background: issue(e.issue_type).color }}>{issue(e.issue_type).label}</span>
          <h2 className="story-title">{headline(e)}</h2>
          <div className="muted small">{fmtTime(e.start, tz)} · {e.priority} priority</div>
          {e.rooms.length > 0 ? (
            <div className="mini"><Map highlight={e.rooms} /></div>
          ) : (
            <div className="involved">
              <div className="muted small">Involved</div>
              {equipment.map((a) => <span key={a} className="chip">{name(a)}</span>)}
            </div>
          )}
          <button className="link" onClick={() => onExplore(e.id)}>Open this incident in Explore →</button>
        </aside>

        <div className="steps">
          <ol className="stepbar">
            {STEPS.map((t, k) => (
              <li key={k} className={k === step ? "now" : k < step ? "done" : ""}>
                <button onClick={() => setStep(k)}><span className="num">{k + 1}</span><span className="t">{t}</span></button>
              </li>
            ))}
          </ol>
          <div className="stepbody">
            <h3>{STEPS[step]}</h3>
            <StepBody k={step} e={e} tz={tz} outcome={outcome} big />
          </div>
          <div className="stepnav">
            <button className="chip" onClick={back} disabled={idx === 0 && step === 0}>← Back</button>
            <span className="muted small">Case {idx + 1} of {cases.length} · step {step + 1} of {STEPS.length} · arrow keys work too</span>
            <button className="chip primary" onClick={next} disabled={idx === cases.length - 1 && step === STEPS.length - 1}>
              {step < STEPS.length - 1 ? "Next step →" : "Next case →"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
