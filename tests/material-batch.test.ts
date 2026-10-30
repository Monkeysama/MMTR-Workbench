import test from "node:test";
import assert from "node:assert/strict";
import { defaultVariantMaster, materialActions, terminalJob, variantMasterFor, type MaterialDraft } from "../src/materialBatch.ts";

const parameter: MaterialDraft = { id: 1, action: "parameter", name: " P ", byteSize: 8,
  values: "1, -2.5", path: "" };
const texture: MaterialDraft = { id: 2, action: "texture", name: "Map", byteSize: 4,
  values: "", path: "systems/rendering/NullBlack.tex" };

test("混合动作保持顺序和默认值，不把纹理当作参数", () => {
  assert.deepEqual(materialActions([texture, parameter]), [
    { action: "texture", name: "Map", path: texture.path },
    { action: "parameter", name: "P", byte_size: 8, values: [1, -2.5] },
  ]);
  assert.equal(parameter.name, " P ");
  assert.deepEqual(materialActions([{ ...parameter, values: "1，2" }])[0],
    { action: "parameter", name: "P", byte_size: 8, values: [1, 2] });
});
test("不悄悄填充空值、截短向量或接受非有限值", () => {
  for (const values of ["", "1", "1,2,3", "NaN,0", "Infinity,0"]) {
    assert.throws(() => materialActions([{ ...parameter, values }]));
  }
  assert.throws(() => materialActions([{ ...parameter, name: "1bad" }]));
  assert.throws(() => materialActions([{ ...texture, path: " " }]));
  assert.throws(() => materialActions([]));
});
test("取消中仍在运行，成功/失败/过期才是终态", () => {
  for (const state of ["queued", "running", "cancelling"] as const) {
    assert.equal(terminalJob({ job_id: "test", state, stage: "", completed: 0, total: 0,
      elapsed_seconds: 0, error: null }), false);
  }
  for (const state of ["succeeded", "cancelled", "failed", "stale"] as const) {
    assert.equal(terminalJob({ job_id: "test", state, stage: "", completed: 0, total: 0,
      elapsed_seconds: 0, error: null }), true);
  }
});

test("独立变体默认 master 随 MDF 游戏切换，不覆盖手动填写的值", () => {
  assert.equal(defaultVariantMaster(), "MaterialShader/Variation/variant.mmtr");
  assert.equal(defaultVariantMaster("RE4"), "_Chainsaw/MasterMaterial/Variation/variant.mmtr");
  assert.equal(defaultVariantMaster("SF6"), "product/mastermaterial/Variation/variant.mmtr");
  assert.equal(defaultVariantMaster("RE8"), "_ge/mastermaterial/master/Variation/variant.mmtr");
  for (const game of ["RE2RT", "MHRISE", "RE3", "RE2"]) {
    assert.equal(defaultVariantMaster(game), "mastermaterial/master/Variation/variant.mmtr");
  }
  assert.equal(variantMasterFor("MaterialShader/Variation/variant.mmtr", "RE4"), defaultVariantMaster("RE4"));
  assert.equal(variantMasterFor(defaultVariantMaster("RE4"), "MHWilds"), defaultVariantMaster("MHWilds"));
  assert.equal(variantMasterFor("MaterialShader/Custom/a.mmtr", "RE4"), "MaterialShader/Custom/a.mmtr");
});
