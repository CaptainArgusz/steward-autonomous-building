import React, { useState } from "react";
import { LineChart } from "./charts.jsx";
import { fmtTime, issue } from "./util.js";
import { action, agentFor, cap, evidence, firstSentence, friendly, headline, light, name, problem } from "./plain.js";

const uniq = (xs) => [...new Set(xs.filter(Boolean))];

export function Light({ autonomy, short = false }) {
  const l = light(autonomy);
  return <span className={`light ${l.cls}`} title={l.note}>{short ? l.short : l.label}</span>;
}

/**
 * Approve / reject for one decision. The answer is kept in the browser only: it is a simulated
 * sign-off, nothing is sent and the replay does not change. Automatic actions are acknowledged.
 */
export function Approval({ d, answers, onAnswer }) {
  const a = answers?.[d.event_id];
  if (a)
    return (
      <span className={`answer ${a}`}>
        {a === "approved" ? "✓ Approved" : a === "rejected" ? "✕ Rejected" : "✓ Acknowledged"} (simulated)
        <button className="link" onClick={() => onAnswer(d.event_id, null)}>Undo</button>
      </span>
    );
  if (d.autonomy === "autonomous")
    return <button className="chip primary" onClick={() => onAnswer(d.event_id, "seen")}>Acknowledge</button>;
  return (
    <>
      <button className="chip primary" onClick={() => onAnswer(d.event_id, "approved")}>Approve</button>
      <button className="chip" onClick={() => onAnswer(d.event_id, "rejected")}>Reject</button>
    </>
  );
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
export function StepBody({ k, e, tz, outcome, big = false, answers, onAnswer }) {
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
        {onAnswer && p.decisions.some((d) => d.autonomy !== "autonomous") && (
          <div className="signoff">
            <b>Your decision</b>
            <div className="muted small">Simulated sign-off: nothing is sent and the replay does not change.</div>
            {p.decisions.filter((d) => d.autonomy !== "autonomous").map((d) => (
              <div className="signrow" key={d.event_id}>
                <span>{action(d.action).icon} <b>{action(d.action).label}</b>{d.resource_id ? ` · ${name(d.resource_id)}` : ""}<span className="muted small"> · {fmtTime(d.timestamp_utc, tz)}{d.autonomy === "approval_and_consent" ? " · guest consent also needed" : ""}</span></span>
                <span className="ask-btns"><Approval d={d} answers={answers} onAnswer={onAnswer} /></span>
              </div>
            ))}
          </div>
        )}
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

/** One incident told in five steps; raw decisions and events stay behind a disclosure. */
export function IncidentStory({ e, tz, outcome, Technical, locator, answers, onAnswer }) {
  const [tech, setTech] = useState(false);
  return (
    <div className="card story">
      <div className="story-top">
        <div>
          <div className="eyebrow" style={{ marginBottom: 10 }}>2 · How Steward handled it</div>
          <div className="story-head">
            <span className="type-pill" style={{ background: issue(e.issue_type).color }}>{issue(e.issue_type).label}</span>
            <span className={`badge p-${e.priority}`}>{e.priority} priority</span>
            <span className="muted small">{fmtTime(e.start, tz)}{e.end !== e.start ? ` → ${fmtTime(e.end, tz)}` : ""}</span>
          </div>
          <h2 className="story-title">{headline(e)}</h2>
        </div>
        {locator && <div className="mini"><div className="cap">Where in the building</div>{locator}</div>}
      </div>
      {STEPS.map((t, k) => (
        <section className="step" key={k}>
          <div className="num">{k + 1}</div>
          <div className="body"><h3>{t}</h3><StepBody k={k} e={e} tz={tz} outcome={outcome} answers={answers} onAnswer={onAnswer} /></div>
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
