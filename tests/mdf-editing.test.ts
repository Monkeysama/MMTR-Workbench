import assert from "node:assert/strict";
import test from "node:test";
import { mdfEditValues } from "../src/utils/mdfDisplay.ts";
import { flagFields, replaceFlag } from "../src/utils/mdfSettings.ts";
import { propertyControl, colorComponents, colorHex, linearToSrgb, srgbToLinear } from "../src/utils/mdfPropertyControls.ts";
import { serialEdits } from "../src/utils/serialEdits.ts";

test("editable decimals round-trip original float32 bytes, including negative zero", () => {
  const bits = new DataView(new ArrayBuffer(4));
  const samples = [0, -0, .95, .40784314, .5019608, 1e-44, -3.4e38, 1.2345679];
  // Cover normal, subnormal, negative, and boundary exponents deterministically.
  for (let i = 1; i < 2048; i++) {
    bits.setUint32(0, Math.imul(i, 0x45d9f3b) >>> 0);
    const value = bits.getFloat32(0);
    if (Number.isFinite(value)) samples.push(value);
  }
  for (const sample of samples) {
    const original = Math.fround(sample);
    assert.ok(Object.is(Math.fround(Number(mdfEditValues([original]))), original));
  }
  assert.equal(mdfEditValues([Math.fround(.95), -0]), "0.95, -0");
});

test("individual flag changes preserve the other high and reserved bits", () => {
  const noRayTracing = flagFields[0]!.find(x => x.name === "NoRayTracing")!;
  assert.equal(replaceFlag(0x0800009b, noRayTracing, 1), 0x8800009b);
  const tess = flagFields[0]!.find(x => x.name === "TessFactor")!;
  assert.equal(replaceFlag(0x8800009b, tess, 31), 0x8800f89b);
  const priority = flagFields[1]!.find(x => x.name === "TransparentPriorityBias")!;
  assert.equal(replaceFlag(0xabcd0081, priority, 255), 0xabcdff81);
});

test("RE Mesh property controls distinguish color, vector and boolean conventions", () => {
  assert.equal(propertyControl({ name: "Emissive_Color", count: 4 }), "color");
  assert.equal(propertyControl({ name: "ColorParam", count: 4 }), "color");
  assert.equal(propertyControl({ name: "Tint_Col_Main", count: 4 }), "color");
  assert.equal(propertyControl({ name: "ColorRate", count: 4 }), "vector");
  assert.equal(propertyControl({ name: "ColorLayer_Blend", count: 1 }), "float");
  for (const name of ["Use_BaseColor", "isVisible", "A_or_B", "BackFaceNormalFilp", "uv1or2_AlphaMap"]) assert.equal(propertyControl({ name, count: 1 }), "boolean");
  assert.equal(propertyControl({ name: "UseOffsets", count: 4 }), "vector");
  assert.equal(propertyControl({ name: "UV_ScaleOffset_R", count: 4 }), "vector");
});

test("color selection converts sRGB to linear RGB without an alpha update", () => {
  assert.equal(colorHex([1, 0, 0, .3]), "#ff0000");
  assert.equal(colorHex([4, -1, 0, .3]), "#ff0000");
  assert.deepEqual(colorComponents("#ff0000"), { 0: 1, 1: 0, 2: 0 });
  assert.equal(colorComponents("rgb(1, 0, 0)"), null);
  assert.ok(Math.abs(colorComponents("#808080")![0]! - .2158605001) < 1e-9);
  for (const value of [0, .001, .0031308, .1, .5, 1, 4]) assert.ok(Math.abs(srgbToLinear(linearToSrgb(value)) - value) < 1e-7);
});

test("automatic edits are serialized, drain includes new edits and failures do not drop the next edit", async () => {
  const queue = serialEdits();
  const order: number[] = [];
  let release!: () => void;
  const block = new Promise<void>(resolve => { release = resolve; });
  const first = queue.enqueue(async () => { await block; order.push(1); });
  const drained = queue.drain();
  const second = queue.enqueue(async () => { order.push(2); });
  assert.equal(queue.pending, 2);
  release();
  await drained;
  await Promise.all([first, second]);
  assert.deepEqual(order, [1, 2]);
  const failed = queue.enqueue(async () => { throw Error("validation error"); });
  const third = queue.enqueue(async () => { order.push(3); });
  await assert.rejects(failed);
  await third;
  assert.equal(queue.pending, 0);
  assert.deepEqual(order, [1, 2, 3]);
});
