import test from "node:test";
import assert from "node:assert/strict";
import { originalLinesAt, traceIsCurrent } from "../src/lib/sourceTrace.ts";
import type { ReconstructionTrace } from "../src/types.ts";

const baseline = { source: "float a;\nreturn a;\n", entry: "main", profile: "ps_6_6" };
function trace(): ReconstructionTrace {
  return { baseline: { ...baseline }, ir: "header\nload\ncast\nstore\nreturn\n", sourceMap: [[], [4, 2, 3, 3]],
    shaderIndex: 228, shaderSha256: "origin-digest", documentSource: "original.mmtr.240906211" };
}
test("maps exact source to distinct absolute original rows and leaves declarations unmapped", () => {
  const t = trace();
  assert.equal(traceIsCurrent(t, baseline), true);
  assert.deepEqual(originalLinesAt(t, baseline, 1), []);
  assert.deepEqual(originalLinesAt(t, baseline, 2), [2, 3, 4]);
  assert.deepEqual(originalLinesAt(t, baseline, 3), []);
});
test("any source, entry or profile edit disables the stale map; exact undo restores it", () => {
  const t = trace();
  for (const source of [{ ...baseline, source: baseline.source + "\n" }, { ...baseline, entry: "other" },
    { ...baseline, profile: "ps_6_2" }, { ...baseline, source: baseline.source.replace("a", "b") }]) {
    assert.equal(traceIsCurrent(t, source), false);
    assert.deepEqual(originalLinesAt(t, source, 2), []);
  }
  assert.deepEqual(originalLinesAt(t, { ...baseline }, 2), [2, 3, 4]);
});
test("missing map, invalid caret lines and out of range original rows cannot navigate", () => {
  const t = trace(); t.sourceMap[1] = [0, -1, 3.2, Number.NaN, 99, 4];
  assert.deepEqual(originalLinesAt(t, baseline, 2), [4]);
  for (const n of [0, -1, 2.1, Number.NaN, 999]) assert.deepEqual(originalLinesAt(t, baseline, n), []);
  assert.deepEqual(originalLinesAt(null, baseline, 2), []);
  assert.equal(traceIsCurrent(null, baseline), false);
});
test("origin snapshot stays independent of subsequent source objects and target selection", () => {
  const t = trace(); const edited = { ...baseline, source: "different shader" };
  assert.deepEqual(originalLinesAt(t, edited, 2), []);
  assert.equal(t.baseline.source, baseline.source);
  assert.equal(t.shaderIndex, 228);
  assert.equal(t.ir, "header\nload\ncast\nstore\nreturn\n");
});
