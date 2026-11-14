import assert from "node:assert/strict";
import test from "node:test";

import { activeAt, firstMajorFloor, layoutFloor, strongestEffect } from "./timeline.js";

const rooms = Array.from({ length: 10 }, (_, i) => ({
  id: `R1${String(i + 1).padStart(2, "0")}`,
  floor: 1,
  orientation: i < 5 ? "S" : "N",
  area_m2: [22, 28, 32][i % 3],
}));

test("floor layout creates ten rooms, door targets, and normalized sides", () => {
  const model = layoutFloor(rooms);
  assert.equal(Object.keys(model.bounds).length, 10);
  assert.deepEqual(model.bounds.R101.door.length, 3);
  for (const side of ["N", "S"]) {
    const width = rooms.filter((r) => r.orientation === side)
      .reduce((total, r) => total + model.bounds[r.id].width, 0);
    assert.ok(Math.abs(width - model.length) < 1e-9);
  }
});

test("initial floor follows the first major event and otherwise uses floor one", () => {
  assert.equal(firstMajorFloor({ effects: [] }), 1);
  assert.equal(firstMajorFloor({ effects: [
    { id: "later", floor: 4, major: true, start_utc: "2026-01-01T12:00:00Z", priority: "critical" },
    { id: "first", floor: 2, major: true, start_utc: "2026-01-01T10:00:00Z", priority: "high" },
  ] }), 2);
});

test("active interval boundaries and strongest effect are deterministic", () => {
  const now = Date.parse("2026-01-01T10:30:00Z");
  assert.equal(activeAt({ start_utc: "2026-01-01T10:00:00Z", end_utc: "2026-01-01T11:00:00Z" }, now), true);
  const effects = [
    { id: "b", priority: "high", start_utc: "2026-01-01T09:00:00Z" },
    { id: "a", priority: "critical", start_utc: "2026-01-01T10:00:00Z" },
  ];
  assert.equal(strongestEffect(effects).id, "a");
});

