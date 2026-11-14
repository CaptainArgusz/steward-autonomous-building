export const PRIORITY = { critical: 3, high: 2, medium: 1, low: 0 };

export function layoutFloor(rooms) {
  const length = 18;
  const depth = 4.6;
  const corridor = 2.4;
  const bounds = {};
  for (const orientation of ["N", "S"]) {
    const side = rooms.filter((r) => r.orientation === orientation).sort((a, b) => a.id.localeCompare(b.id));
    const weights = side.map((r) => Math.sqrt(r.area_m2 || 25));
    const scale = length / weights.reduce((a, b) => a + b, 0);
    let x = -length / 2;
    for (let i = 0; i < side.length; i += 1) {
      const width = weights[i] * scale;
      const sign = orientation === "N" ? -1 : 1;
      const z = sign * (corridor / 2 + depth / 2);
      bounds[side[i].id] = {
        room: side[i], x: x + width / 2, z, width, depth, sign,
        door: [x + width / 2, 0.2, sign * corridor / 2],
        task: [x + width / 2, 0.34, z],
      };
      x += width;
    }
  }
  return { bounds, length, depth, corridor, totalDepth: depth * 2 + corridor, origin: [-length / 2 + 0.45, 0.34, 0] };
}

export function activeAt(item, now, start = "start_utc", end = "end_utc") {
  const t = Number(now);
  return t >= Date.parse(item[start]) && t <= Date.parse(item[end]);
}

export function firstMajorFloor(info) {
  const first = [...(info?.effects || [])]
    .filter((e) => e.major && e.floor)
    .sort((a, b) => Date.parse(a.start_utc) - Date.parse(b.start_utc)
      || (PRIORITY[b.priority] || 0) - (PRIORITY[a.priority] || 0) || a.id.localeCompare(b.id))[0];
  return first?.floor || 1;
}

export function strongestEffect(effects) {
  return [...effects].sort((a, b) => (PRIORITY[b.priority] || 0) - (PRIORITY[a.priority] || 0)
    || Date.parse(a.start_utc) - Date.parse(b.start_utc) || a.id.localeCompare(b.id))[0] || null;
}

