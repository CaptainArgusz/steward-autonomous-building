import React, { useEffect, useMemo, useRef, useState } from "react";
import { fmtTime } from "./util.js";

// Categorical slots in fixed order (never cycled); two series is the most we draw per chart.
const SERIES = ["var(--water)", "var(--heat)", "var(--turn)", "var(--logi)"];

function useWidth() {
  const ref = useRef(null);
  const [w, setW] = useState(520);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function niceTicks(lo, hi, n = 4) {
  const span = hi - lo || 1;
  const step0 = span / n;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

/** Time-series line chart: one y axis, gaps where data is missing, decision times marked. */
export function LineChart({ chart, markers = [], tz }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const H = 150, m = { l: 38, r: 10, t: 8, b: 20 };

  const model = useMemo(() => {
    const series = chart.series.map((s) => ({
      name: s.name,
      pts: s.points.map(([t, v]) => [Date.parse(t), v]),
    }));
    const xs = series.flatMap((s) => s.pts.map((p) => p[0]));
    const ys = series.flatMap((s) => s.pts.map((p) => p[1])).filter((v) => v != null);
    if (chart.band) ys.push(...chart.band);
    if (!xs.length || !ys.length) return null;
    let lo = Math.min(...ys), hi = Math.max(...ys);
    if (chart.unit !== "°C") lo = Math.min(0, lo);
    if (hi === lo) hi = lo + 1;
    const pad = (hi - lo) * 0.08;
    return { series, x0: Math.min(...xs), x1: Math.max(...xs), lo: lo - (lo === 0 ? 0 : pad), hi: hi + pad };
  }, [chart]);

  if (!model) return <div className="chart muted small" ref={ref}>{chart.title}: no data in this window</div>;
  const { series, x0, x1, lo, hi } = model;
  const X = (t) => m.l + ((t - x0) / (x1 - x0 || 1)) * (width - m.l - m.r);
  const Y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);

  const path = (pts) => {
    let d = "", pen = false;
    for (const [t, v] of pts) {
      if (v == null) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${X(t).toFixed(1)},${Y(v).toFixed(1)}`;
      pen = true;
    }
    return d;
  };
  const days = [];
  for (let t = Math.ceil(x0 / 864e5) * 864e5; t <= x1; t += 864e5) days.push(t);

  const onMove = (e) => {
    const box = e.currentTarget.getBoundingClientRect();
    const t = x0 + ((e.clientX - box.left - m.l) / (width - m.l - m.r)) * (x1 - x0);
    const ref0 = series[0].pts;
    let best = 0;
    for (let i = 1; i < ref0.length; i++) if (Math.abs(ref0[i][0] - t) < Math.abs(ref0[best][0] - t)) best = i;
    setHover(best);
  };
  const ht = hover != null ? series[0].pts[hover]?.[0] : null;

  return (
    <div className="chart" ref={ref}>
      <div className="ct">
        <b>{chart.title}</b>
        <span className="muted">{chart.unit}</span>
      </div>
      <div className="chartwrap">
        <svg height={H} onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img" aria-label={chart.title}>
          {(chart.occupied || []).map(([a, b], i) => {
            const xa = Math.max(X(Date.parse(a)), m.l), xb = Math.min(X(Date.parse(b)), width - m.r);
            return xb > xa ? <rect key={i} x={xa} y={m.t} width={xb - xa} height={H - m.t - m.b} fill="var(--occupied)" /> : null;
          })}
          {chart.band && (
            <rect x={m.l} y={Y(chart.band[1])} width={width - m.l - m.r} height={Y(chart.band[0]) - Y(chart.band[1])} fill="var(--band)" />
          )}
          <g className="axis">
            {niceTicks(lo, hi).map((v) => (
              <g key={v}>
                <line className="grid" x1={m.l} x2={width - m.r} y1={Y(v)} y2={Y(v)} />
                <text x={m.l - 5} y={Y(v) + 3} textAnchor="end">{v}</text>
              </g>
            ))}
            {days.filter((_, i) => i % Math.ceil(days.length / 6) === 0).map((t) => (
              <text key={t} x={X(t)} y={H - 5} textAnchor="middle">
                {new Date(t).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" })}
              </text>
            ))}
          </g>
          {series.map((s, i) => (
            <path key={s.name} d={path(s.pts)} fill="none" stroke={SERIES[i]} strokeWidth="2" strokeLinejoin="round"
              strokeDasharray={s.name === "Setpoint" ? "4 3" : undefined} />
          ))}
          {markers.map((t, i) => {
            const x = X(Date.parse(t));
            return x >= m.l && x <= width - m.r ? (
              <line key={i} x1={x} x2={x} y1={m.t} y2={H - m.b} stroke="var(--text)" strokeWidth="1.5" strokeDasharray="2 3" />
            ) : null;
          })}
          {ht != null && <line x1={X(ht)} x2={X(ht)} y1={m.t} y2={H - m.b} stroke="var(--text-3)" strokeWidth="1" />}
          {ht != null && series.map((s, i) => s.pts[hover]?.[1] != null && (
            <circle key={i} cx={X(ht)} cy={Y(s.pts[hover][1])} r="4" fill={SERIES[i]} stroke="var(--surface)" strokeWidth="2" />
          ))}
        </svg>
        {ht != null && (
          <div className="tip" style={{ left: X(ht), top: m.t }}>
            {fmtTime(new Date(ht).toISOString(), tz)} ·{" "}
            {series.map((s) => `${s.name} ${s.pts[hover]?.[1] ?? "missing"}`).join(" · ")} {chart.unit}
          </div>
        )}
      </div>
      <div className="legend">
        {series.length > 1 && series.map((s, i) => (
          <span key={s.name}><i className="swatch" style={{ background: SERIES[i] }} />{s.name}</span>
        ))}
        {chart.occupied && <span><i className="swatch" style={{ background: "var(--occupied)", border: "1px solid var(--line)" }} />Room occupied</span>}
        {chart.band && <span><i className="swatch" style={{ background: "var(--band)", border: "1px solid var(--line)" }} />Comfort band</span>}
        {markers.length > 0 && <span>┆ decision taken (data to the right was not yet known)</span>}
      </div>
    </div>
  );
}

/** Line chart over ordered categories (e.g. outdoor-temperature bins, hours of the day). */
export function CategoryLine({ chart }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const H = 170, m = { l: 40, r: 12, t: 12, b: 22 };
  const cats = chart.categories || [], series = chart.series || [];
  const vals = series.flatMap((s) => s.values).filter((v) => v != null);
  if (!cats.length || !vals.length) return null;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo > 0 && lo < hi * 0.5) lo = 0;
  const pad = (hi - lo || 1) * 0.08; lo -= lo === 0 ? 0 : pad; hi += pad;
  const X = (i) => m.l + (cats.length === 1 ? 0.5 : i / (cats.length - 1)) * (width - m.l - m.r);
  const Y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
  const every = Math.ceil(cats.length / 8);
  return (
    <div className="chart" ref={ref}>
      <div className="ct"><b>{chart.title}</b><span className="muted">{chart.unit}</span></div>
      <div className="chartwrap">
        <svg height={H} role="img" aria-label={chart.title} onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const x = e.clientX - e.currentTarget.getBoundingClientRect().left;
            setHover(Math.max(0, Math.min(cats.length - 1, Math.round(((x - m.l) / (width - m.l - m.r)) * (cats.length - 1)))));
          }}>
          <g className="axis">
            {niceTicks(lo, hi).map((v) => (
              <g key={v}><line className="grid" x1={m.l} x2={width - m.r} y1={Y(v)} y2={Y(v)} /><text x={m.l - 5} y={Y(v) + 3} textAnchor="end">{v}</text></g>
            ))}
            {cats.map((c, i) => i % every === 0 && <text key={i} x={X(i)} y={H - 6} textAnchor="middle">{String(c)}</text>)}
          </g>
          {series.map((s, si) => (
            <g key={s.name}>
              <path fill="none" stroke={SERIES[si % 4]} strokeWidth="2" strokeLinejoin="round"
                d={s.values.reduce((d, v, i) => (v == null ? d : `${d}${d && s.values[i - 1] != null ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`), "")} />
              {cats.length <= 12 && s.values.map((v, i) => v != null && <circle key={i} cx={X(i)} cy={Y(v)} r="4" fill={SERIES[si % 4]} stroke="var(--surface)" strokeWidth="2" />)}
            </g>
          ))}
          {hover != null && <line x1={X(hover)} x2={X(hover)} y1={m.t} y2={H - m.b} stroke="var(--text-3)" strokeWidth="1" />}
        </svg>
        {hover != null && (
          <div className="tip" style={{ left: X(hover), top: m.t }}>
            {String(cats[hover])} · {series.map((s) => `${s.name} ${s.values[hover] ?? "–"}`).join(" · ")} {chart.unit}
          </div>
        )}
      </div>
      {series.length > 1 && (
        <div className="legend">{series.map((s, i) => <span key={s.name}><i className="swatch" style={{ background: SERIES[i % 4] }} />{s.name}</span>)}</div>
      )}
    </div>
  );
}

/** Grouped bar chart for already-aggregated insight evidence. */
export function BarChart({ chart }) {
  if (chart.kind === "line") return <CategoryLine chart={chart} />;
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const H = 170, m = { l: 40, r: 8, t: 14, b: 22 };
  const cats = chart.categories || [];
  const series = chart.series || [];
  const vals = series.flatMap((s) => s.values).filter((v) => v != null);
  if (!cats.length || !vals.length) return null;
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals) || 1;
  const Y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
  const band = (width - m.l - m.r) / cats.length;
  const bw = Math.min(28, (band - 10) / series.length - 2);
  const fmt = (v) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2));

  return (
    <div className="chart" ref={ref}>
      <div className="ct"><b>{chart.title}</b><span className="muted">{chart.unit}</span></div>
      <div className="chartwrap">
        <svg height={H} role="img" aria-label={chart.title}>
          <g className="axis">
            {niceTicks(lo, hi).map((v) => (
              <g key={v}>
                <line className="grid" x1={m.l} x2={width - m.r} y1={Y(v)} y2={Y(v)} />
                <text x={m.l - 5} y={Y(v) + 3} textAnchor="end">{v}</text>
              </g>
            ))}
            {cats.map((c, i) => (
              <text key={i} x={m.l + band * (i + 0.5)} y={H - 6} textAnchor="middle">{String(c)}</text>
            ))}
          </g>
          {series.map((s, si) => s.values.map((v, ci) => {
            if (v == null) return null;
            const x = m.l + band * (ci + 0.5) - (series.length * (bw + 2)) / 2 + si * (bw + 2) + 1;
            const y0 = Y(0), y1 = Y(v);
            const key = `${si}-${ci}`;
            return (
              <g key={key} onMouseEnter={() => setHover(key)} onMouseLeave={() => setHover(null)}>
                <rect x={x - 2} y={m.t} width={bw + 4} height={H - m.t - m.b} fill="transparent" />
                <rect x={x} y={Math.min(y0, y1)} width={bw} height={Math.max(1, Math.abs(y1 - y0))} rx="3" fill={SERIES[si % 4]}
                  opacity={hover && hover !== key ? 0.55 : 1} />
                {(series.length === 1 || hover === key) && (
                  <text x={x + bw / 2} y={Math.min(y0, y1) - 3} textAnchor="middle" fontSize="10" fill="var(--text-2)">{fmt(v)}</text>
                )}
              </g>
            );
          }))}
        </svg>
      </div>
      {series.length > 1 && (
        <div className="legend">
          {series.map((s, i) => <span key={s.name}><i className="swatch" style={{ background: SERIES[i % 4] }} />{s.name}</span>)}
        </div>
      )}
    </div>
  );
}
