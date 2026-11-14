import React from "react";
import { issue } from "./util.js";
import { action, isIncident } from "./plain.js";

/** What became of each problem the replay recorded, best case first. */
const FATE = [
  ["flagged_before", "var(--f-before)", "flagged beforehand"],
  ["dismissed_with_reason", "var(--f-judged)", "checked, not a fault"],
  ["acted_after", "var(--f-after)", "handled as it happened"],
  ["not_seen", "var(--f-unseen)", "missed"],
];

/** Donut with a number in the middle. ``parts`` is [[value, colour], ...]. */
function Ring({ parts, big, label }) {
  const total = parts.reduce((a, [v]) => a + v, 0) || 1;
  const R = 52, C = 2 * Math.PI * R, gap = parts.filter(([v]) => v > 0).length > 1 ? 3 : 0;
  let at = 0;
  return (
    <div className="ring">
      <svg viewBox="0 0 128 128" role="img" aria-label={`${big} ${label}`}>
        <circle cx="64" cy="64" r={R} fill="none" stroke="var(--surface-2)" strokeWidth="14" />
        {parts.map(([v, color], i) => {
          const len = (v / total) * C, off = at;
          at += len;
          return v > 0 && <circle key={i} cx="64" cy="64" r={R} fill="none" stroke={color} strokeWidth="14"
            strokeDasharray={`${Math.max(0, len - gap)} ${C}`} strokeDashoffset={-off} transform="rotate(-90 64 64)" />;
        })}
      </svg>
      <div className="mid"><b>{big}</b><span>{label}</span></div>
    </div>
  );
}

function Question({ n, q, tone, visual, children }) {
  return (
    <section className={`card question ${tone}`}>
      <div className="qhead">
        <div className="eyebrow">Question {n}</div>
        <h2>{q}</h2>
        {visual}
      </div>
      <div className="qbody">{children}</div>
    </section>
  );
}

/** Incidents per month (or per day for a short dataset), as [label, count]. */
function perPeriod(incidents, meta, tz) {
  const daily = (Date.parse(meta.end) - Date.parse(meta.start)) / 864e5 <= 62;
  const fmt = new Intl.DateTimeFormat("en-GB", { timeZone: tz, ...(daily ? { day: "numeric", month: "short" } : { month: "short" }) });
  const out = [];
  for (let t = Date.parse(meta.start); t < Date.parse(meta.end); t += daily ? 864e5 : 864e5 * 27) {
    const k = fmt.format(new Date(t));
    if (!out.length || out[out.length - 1][0] !== k) out.push([k, 0]);
  }
  for (const e of incidents) {
    const row = out.find(([k]) => k === fmt.format(new Date(e.start)));
    if (row) row[1] += 1;
  }
  return { rows: out, daily };
}

/** Track record: three questions an operator would ask, each answered with one picture. */
export function Record({ data, tz }) {
  const sc = data.scorecard;
  const all = (sc?.outcomes || []).filter((o) => o.recorded > 0).sort((a, b) => b.recorded - a.recorded);
  const outcomes = all.slice(0, 5); // the page shows the five most frequent kinds; totals cover all of them
  const detailed = all.some((o) => o.not_seen != null);
  const sum = (k) => all.reduce((a, o) => a + (o[k] || 0), 0);
  const recorded = sum("recorded");
  const fate = detailed ? FATE : FATE.slice(0, 1);

  const incidents = data.episodes.filter(isIncident);
  const perWeek = sc?.weeks ? incidents.length / sc.weeks : null;
  const period = perPeriod(incidents, data.meta, tz);
  const peak = Math.max(1, ...period.rows.map(([, n]) => n));
  const kinds = [...new Set(data.dismissed.map((d) => d.issue_type))]
    .map((t) => [t, data.dismissed.filter((d) => d.issue_type === t)]).sort((a, b) => b[1].length - a[1].length);

  const decisions = data.episodes.flatMap((e) => e.decisions);
  const auto = decisions.filter((d) => d.autonomy === "autonomous").length;
  const asked = decisions.length - auto;
  const held = decisions.filter((d) => d.status === "refused_safety_check" || d.status === "blocked_by_dependency").length;
  const asks = decisions.filter((d) => d.autonomy !== "autonomous");
  const span = Date.parse(data.meta.end) - Date.parse(data.meta.start);
  const everyDays = asks.length ? Math.round(span / 864e5 / asks.length) : null;
  const pos = (iso) => `${Math.max(0, Math.min(100, ((Date.parse(iso) - Date.parse(data.meta.start)) / span) * 100))}%`;
  // what it asked for: per action, how many times, and who has to say yes
  const askedFor = Object.entries(asks.reduce((m, d) => ({ ...m, [d.action]: (m[d.action] || 0) + 1 }), {})).sort((x, y) => y[1] - x[1]);
  const maxAsk = Math.max(1, ...askedFor.map(([, n]) => n));

  return (
    <div className="page">
      <div className="page-head">
        <h1>Can you trust it?</h1>
        <p>Three questions an operator would ask before letting Steward act, answered from what the replay itself recorded.</p>
      </div>

      <Question n="1" tone="t-green" q="Did it speak up before things went wrong?"
        visual={recorded > 0 && (
          <>
            <Ring parts={fate.map(([k, c]) => [sum(k), c]).concat(detailed ? [] : [[recorded - sum("flagged_before"), "var(--surface-2)"]])} big={sum("flagged_before")} label={`of ${recorded} flagged early`} />
            <ul className="keylist">
              {fate.map(([k, c, label]) => <li key={k}><i style={{ background: c }} /><b>{sum(k)}</b>{label}</li>)}
            </ul>
          </>
        )}>
        {!recorded ? <div className="empty">This dataset records no problems to compare with.</div> : (
          <>
            <div className="bars">
              {outcomes.map((r) => (
                <div className="barrow" key={r.key} title={fate.filter(([k]) => r[k] > 0).map(([k, , label]) => `${r[k]} ${label}`).join(" · ")}>
                  <span className="what">{r.label}</span>
                  <span className="fatebar">
                    {fate.map(([k, c]) => r[k] > 0 && <i key={k} style={{ flex: r[k], background: c }} />)}
                    {!detailed && r.recorded - r.flagged_before > 0 && <i style={{ flex: r.recorded - r.flagged_before, background: "var(--surface-2)" }} />}
                  </span>
                  <span className="num">
                    <b>{r.flagged_before}</b> early{r.dismissed_with_reason > 0 && <> · <b>{r.dismissed_with_reason}</b> not a fault</>} <span>of {r.recorded}</span>
                  </span>
                </div>
              ))}
            </div>
            {all.length > outcomes.length && <p className="note">The {all.length - outcomes.length} rarer kinds are in the total on the left.</p>}
          </>
        )}
      </Question>

      <Question n="2" tone="t-blue" q="Did it stay quiet when nothing was wrong?"
        visual={<div className="bignum"><b>{perWeek != null ? perWeek.toFixed(1) : incidents.length}</b><span>{perWeek != null ? "incidents a week on average" : "incidents raised"}</span></div>}>
        <div className="eyebrow">Incidents raised per {period.daily ? "day" : "month"}</div>
        <div className="months">
          {period.rows.map(([k, n], i) => (
            <div key={k} title={`${k}: ${n}`}>
              <span>{n || ""}</span>
              <i style={{ height: `${(n / peak) * 100}%` }} />
              <em>{!period.daily || i % 3 === 0 ? k : ""}</em>
            </div>
          ))}
        </div>
        {kinds.length > 0 && (
          <>
            <div className="eyebrow" style={{ marginTop: 22 }}>{data.dismissed.length} look-alikes checked and not raised</div>
            <div className="quiet">
              {kinds.map(([t, rows]) => (
                <div key={t}>
                  <b style={{ color: issue(t).color }}>{rows.length}</b>
                  <strong>{issue(t).label}</strong>
                </div>
              ))}
            </div>
          </>
        )}
      </Question>

      <Question n="3" tone="t-amber" q="Did it stay inside its limits?"
        visual={
          <>
            <div className="bignum"><b>{asks.length}</b><span>times it stopped and asked a person{everyDays ? `, about once every ${everyDays} days` : ""}</span></div>
            <ul className="keylist" style={{ marginTop: 14 }}>
              <li><i style={{ background: "var(--good)" }} /><b>{auto}</b>actions done on its own</li>
              <li><i style={{ background: "var(--text)" }} /><b>{data.meta.validator_errors}</b>broke an autonomy rule</li>
              {held > 0 && <li><i style={{ background: "var(--text-3)" }} /><b>{held}</b>held back by the executor's checks</li>}
            </ul>
          </>
        }>
        {asks.length === 0 ? <div className="empty">Steward did not need a person in this dataset.</div> : (
          <>
            <div className="eyebrow">When it asked</div>
            <div className="strip">
              <div className="line">
                {asks.map((d) => <i key={d.event_id} className={d.autonomy === "approval_and_consent" ? "stop" : "wait"} style={{ left: pos(d.timestamp_utc) }} title={`${action(d.action).label} · ${new Date(d.timestamp_utc).toLocaleDateString("en-GB", { timeZone: tz, day: "numeric", month: "short" })}`} />)}
              </div>
              <div className="ticks">{period.rows.filter((_, i) => !period.daily || i % 3 === 0).map(([k]) => <span key={k}>{k}</span>)}</div>
            </div>

            <div className="eyebrow" style={{ marginTop: 24 }}>What it asked for, and who decides</div>
            <div className="asks">
              {askedFor.map(([a, n]) => (
                <div key={a}>
                  <span className="what">{action(a).icon} {action(a).label}</span>
                  <span className="askbar"><i style={{ width: `${(n / maxAsk) * 100}%` }} /></span>
                  <b>{n}</b>
                  <span className="muted small">{action(a).who}</span>
                </div>
              ))}
            </div>
          </>
        )}
      </Question>
    </div>
  );
}
