import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { convertDxil, splitOperands } from "../src/lib/dxilReadable.ts";

function ir(body: string, header = "") {
  return header + "\ndefine void @main() {\n" + body + "\n}\n";
}
function noDangling(code: string) {
  const declared = new Set(
    [...code.matchAll(/\br_\w+\s*=/g)].map((m) => m[0].replace(/\s*=$/, "")),
  );
  const referenced = [...code.matchAll(/\br_\d+\b/g)].map((m) => m[0]);
  assert.deepEqual(
    [...new Set(referenced.filter((r) => !declared.has(r)))],
    [],
  );
}
test("nested operands and exact DXIL math opcodes", () => {
  assert.equal(
    splitOperands("i32 1, {i32 0, i32 2}, [ %1, %entry ]").length,
    3,
  );
  const r = convertDxil(
    ir(
      [
        "%1 = call float @dx.op.unary.f32(i32 23, float 0x4000000000000000)",
        "%2 = call float @dx.op.unary.f32(i32 24, float %1)",
        "%3 = call float @dx.op.tertiary.f32(i32 46, float %2, float 2.0, float 1.0)",
        "%4 = call float @dx.op.tertiary.f32(i32 47, float %3, float 2.0, float 1.0)",
        "ret void",
      ].join("\n"),
    ),
  );
  assert.match(r.code, /log2\(2.0\)/);
  assert.match(r.code, /sqrt\(/);
  assert.match(r.code, /mad\(/);
  assert.match(r.code, /fma\(/);
  assert.equal(r.stats.raw, 0);
  noDangling(r.code);
});
test("bounded inlining keeps definitions required by deeper expressions", () => {
  const body = [
    "%1 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)",
  ];
  for (let i = 2; i < 18; i++)
    body.push("%" + i + " = fmul float %" + (i - 1) + ", 2.0");
  body.push(
    "call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 0, float %17)",
    "ret void",
  );
  const folded = convertDxil(ir(body.join("\n")));
  const expanded = convertDxil(ir(body.join("\n")), { foldExpressions: false });
  noDangling(folded.code);
  noDangling(expanded.code);
  assert.ok(folded.stats.folded > expanded.stats.folded);
  assert.equal(folded.sourceMap.length, folded.code.split("\n").length);
});
test("branches, loop phi, signed values and NaN-sensitive comparisons", () => {
  const r = convertDxil(
    ir(
      [
        "br label %loop",
        "loop:",
        "%1 = phi i32 [ 0, %0 ], [ %2, %loop ]",
        "%2 = add i32 %1, 1",
        "%3 = icmp slt i32 %2, 3",
        "%4 = fcmp one float 0.0, 1.0",
        "br i1 %3, label %loop, label %end, !llvm.loop !9",
        "end:",
        "ret void",
      ].join("\n"),
    ),
  );
  assert.match(r.code, /phi_from\(block_0: 0, block_loop: r_2\)/);
  assert.match(r.code, /int\(/);
  assert.match(r.code, /!isnan/);
  assert.match(r.code, /goto block_loop; else goto block_end/);
  assert.equal(r.stats.raw, 0);
  noDangling(r.code);
});
test("reflected buffer offsets, arrays, matrix orientation and bit reinterpretation", () => {
  const header = [
    "; cbuffer Material",
    "; {",
    "; row_major float3x4 transform; ; Offset: 0",
    "; float2 colors[2]; ; Offset: 48",
    "; uint flags; ; Offset: 80",
    "; }",
    "; Material cbuffer NA NA CB0 cb2,space1 1",
  ].join("\n");
  const r = convertDxil(
    ir(
      [
        "%1 = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind { i32 2, i32 2, i32 1, i8 2 }, i32 2, i1 false)",
        "%2 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %1, i32 1)",
        "%3 = extractvalue %dx.types.CBufRet.f32 %2, 2",
        "%4 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %1, i32 4)",
        "%5 = extractvalue %dx.types.CBufRet.f32 %4, 1",
        "%6 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %1, i32 5)",
        "%7 = extractvalue %dx.types.CBufRet.f32 %6, 0",
        "ret void",
      ].join("\n"),
      header,
    ),
  );
  assert.match(r.code, /Material.transform\[1\]\[2\]/);
  assert.match(r.code, /Material.colors\[1\].y/);
  assert.match(r.code, /asfloat\(Material.flags\)/);
  assert.equal(r.resources[0].binding, "cb2,space1");
});
test("texture sampling names retain gradients, offsets and LOD clamp", () => {
  const header =
    "; SkinMap texture f32 2d T0 t2 1\n; SkinSampler sampler NA NA S0 s3 1";
  const r = convertDxil(
    ir(
      [
        "%1 = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 0, i32 0, i32 2, i1 false)",
        "%2 = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 3, i32 0, i32 3, i1 false)",
        "%3 = call %dx.types.ResRet.f32 @dx.op.sampleGrad.f32(i32 63, %dx.types.Handle %1, %dx.types.Handle %2, float 0.0, float 1.0, float undef, float undef, i32 1, i32 2, i32 undef, float 0.1, float 0.2, float undef, float 0.3, float 0.4, float undef, float 5.0)",
        "%4 = extractvalue %dx.types.ResRet.f32 %3, 0",
        "ret void",
      ].join("\n"),
      header,
    ),
  );
  assert.match(r.code, /\(SkinMap\).SampleGrad\(\s*SkinSampler/);
  assert.match(r.code, /offset: int2\(1, 2\)/);
  assert.match(r.code, /ddx: float2\(0.1, 0.2\)/);
  assert.match(r.code, /ddy: float2\(0.3, 0.4\)/);
  assert.match(r.code, /clamp: 5.0/);
  noDangling(r.code);
});
test("unknown operations are explicit and retain source mapping and dispatch opcode", () => {
  const r = convertDxil(
    ir(
      "%1 = mystery float 1.0\n%2 = call float @dx.op.unary.f32(i32 999, float 1.0)\nret void",
    ),
  );
  assert.match(r.code, /llvm_raw\("mystery float 1.0"\)/);
  assert.match(r.code, /dx.unary\(opcode: 999, 1.0\)/);
  assert.equal(r.stats.raw, 1);
  assert.equal(r.stats.namedDxCalls, 1);
  const line = r.code.split("\n").findIndex((l) => l.includes('llvm_raw("'));
  assert.ok(r.sourceMap[line].includes(3));
});
test("DXIL signature IDs override the order of comment tables", () => {
  const header = [
    "; Input signature:",
    "; WrongGuess 0 x 0 NONE float x",
    "!dx.entryPoints = !{!1}",
    '!1 = !{void ()* @main, !"main", !2, null, null}',
    "!2 = !{!3, null, null}",
    "!3 = !{!4}",
    '!4 = !{i32 0, !"ActualSemantic", i8 9, i8 0, !5}',
    "!5 = !{i32 7}",
  ].join("\n");
  const r = convertDxil(
    ir(
      "%1 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)\nret void",
      header,
    ),
  );
  assert.match(r.code, /input.ActualSemantic7.x/);
  assert.doesNotMatch(r.code, /WrongGuess/);
});
test("dynamic heap textures retain unknown dimensions rather than inventing coordinates", () => {
  const r = convertDxil(
    ir(
      [
        "%1 = call %dx.types.Handle @dx.op.createHandleFromHeap(i32 218, i32 12, i1 false, i1 true)",
        "%2 = call %dx.types.ResRet.f32 @dx.op.sample.f32(i32 60, %dx.types.Handle %1, %dx.types.Handle %1, float 1.0, float 2.0, float 3.0, float 4.0, i32 0, i32 0, i32 0, float 9.0)",
        "ret void",
      ].join("\n"),
    ),
  );
  assert.match(r.code, /ResourceDescriptorHeap\[12\]/);
  assert.match(r.code, /dx.sample\(/);
  assert.ok(!r.code.includes(".Sample("));
  assert.match(r.code, /9.0/);
});
test(
  "all available skin shader fixtures preserve instruction accounting and definitions",
  { skip: !existsSync("output/skin_atos/shaders") },
  () => {
    const files = readdirSync("output/skin_atos/shaders").filter((f) =>
      f.endsWith(".ll"),
    );
    assert.equal(files.length, 538);
    for (const file of files) {
      const r = convertDxil(
        readFileSync("output/skin_atos/shaders/" + file, "utf8"),
      );
      assert.equal(
        r.stats.instructions,
        r.stats.translated + r.stats.namedDxCalls + r.stats.raw,
        file,
      );
      assert.equal(r.stats.raw, 0, file);
      noDangling(r.code);
      assert.ok(
        r.sourceMap.flat().every((n) => n > 0),
        file,
      );
    }
  },
);
