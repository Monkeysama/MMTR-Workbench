/** DXIL -> evidence-preserving HLSL-like pseudocode. Never presented as recovered source. */
export interface ShaderResource {
  name: string;
  kind: string;
  dimension: string;
  id: string;
  binding: string;
  register: number;
  space: number;
  count: string;
}
export interface BufferField {
  buffer: string;
  name: string;
  type: string;
  offset: number;
  count: number;
  major: string;
}
export interface ReadableShader {
  code: string;
  sourceMap: number[][];
  resources: ShaderResource[];
  fields: BufferField[];
  stats: {
    instructions: number;
    translated: number;
    namedDxCalls: number;
    raw: number;
    folded: number;
    blocks: number;
  };
  warnings: string[];
}
interface Instruction {
  id: string;
  body: string;
  comment: string;
  line: number;
  block: string;
  label?: string;
  expression: string;
  type: string;
  category: "translated" | "named" | "raw";
  pure: boolean;
  alias: boolean;
  sources: number[];
  refs: string[];
}
interface FunctionBody {
  name: string;
  start: number;
  end: number;
  signature: string;
  nodes: Instruction[];
}
interface CBufferRead {
  handle: string;
  register: string;
  scalar: string;
  sources: number[];
}
const ssaPattern = /%(?:"[^"\n]+"|[\w.$-]+)/g;
const identifier = (v: string) =>
  v.replace(/^[@%]/, "").replace(/^"|"$/g, "").replace(/[^\w]/g, "_");
const variable = (v: string) => "r_" + identifier(v);
const blockName = (v: string) => "block_" + identifier(v);

/** Respect nested vectors, arrays, phi incoming pairs and ResBind constants. */
export function splitOperands(text: string): string[] {
  const parts: string[] = [];
  let start = 0,
    depth = 0,
    quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"' && text[i - 1] !== "\\") quoted = !quoted;
    if (quoted) continue;
    if ("([{<".includes(c)) depth++;
    if (")]}>".includes(c)) depth--;
    if (c === "," && depth === 0) {
      parts.push(text.slice(start, i).trim());
      start = i + 1;
    }
  }
  parts.push(text.slice(start).trim());
  return parts.filter(Boolean);
}
function typed(text: string): [string, string] {
  text = text.trim();
  const simple = text.match(
    /^(?:%"[^"\n]+"|%[\w.$-]+|i\d+|half|float|double|void|label|ptr)(?:\s+addrspace\(\d+\))?\**\s+/,
  );
  if (simple) return [simple[0].trim(), text.slice(simple[0].length).trim()];
  if ("[{<".includes(text[0] ?? "!")) {
    let depth = 0;
    for (let i = 0; i < text.length; i++) {
      if ("[{<".includes(text[i])) depth++;
      if ("]}>".includes(text[i])) depth--;
      if (!depth) {
        let end = i + 1;
        while (text[end] === "*") end++;
        return [text.slice(0, end), text.slice(end).trim()];
      }
    }
  }
  return ["", text];
}
function scalarType(type: string): string {
  if (type.endsWith("*")) return "ptr<" + scalarType(type.slice(0, -1)) + ">";
  if (type === "i1") return "bool";
  if (/^i\d+$/.test(type))
    return type === "i32" ? "uint" : "uint" + type.slice(1) + "_t";
  if (["float", "half", "double", "void"].includes(type)) return type;
  const vector = type.match(/^<\s*(\d+)\s+x\s+(\w+)\s*>$/);
  if (vector) return scalarType(vector[2]) + vector[1];
  if (type.includes("ResRet")) return "ResourceRead";
  if (type.includes("CBufRet")) return "CBufferRead";
  if (type.includes("Handle")) return "ResourceHandle";
  return "auto";
}
function constant(text: string, type = ""): string {
  if (/^0x[0-9A-Fa-f]{16}$/.test(text) && ["float", "double"].includes(type)) {
    const data = new DataView(new ArrayBuffer(8));
    data.setBigUint64(0, BigInt(text), false);
    const v = data.getFloat64(0, false);
    if (Number.isFinite(v))
      return Object.is(v, -0)
        ? "-0.0"
        : Number.isInteger(v)
          ? String(v) + ".0"
          : String(v);
    return "ieee_float(" + text + ")";
  }
  if (/^-?\d+\.\d+e[+-]?\d+$/i.test(text)) {
    const v = Number(text);
    return Number.isInteger(v) ? String(v) + ".0" : String(v);
  }
  if (text === "undef") return "undefined_value";
  if (text === "poison") return "poison_value";
  return text.replace(/^@/, "");
}
function operand(text: string): string {
  const [type, value] = typed(text);
  return constant(value, type);
}
function parseReflection(lines: string[]) {
  const resources: ShaderResource[] = [],
    fields: BufferField[] = [];
  const input: string[] = [],
    output: string[] = [];
  let buffer = "",
    section = "",
    signaturesDone = false;
  for (const line of lines) {
    const cb = line.match(/^; cbuffer (\S+)/);
    if (cb) buffer = cb[1];
    if (/^; Resource bind info/.test(line) || line.startsWith("target "))
      buffer = "";
    const field = line.match(
      /^;\s+(?:(row_major|column_major)\s+)?((?:float|half|double|uint|int|bool)(?:[1-4](?:x[1-4])?)?)\s+(\w+)(?:\[(\d+)\])?\s*;\s*; Offset:\s*(\d+)/,
    );
    if (buffer && field)
      fields.push({
        buffer,
        major: field[1] || "column_major",
        type: field[2],
        name: field[3],
        count: Number(field[4] ?? 1),
        offset: Number(field[5]),
      });
    const resource = line.match(
      /^;\s*(\S+)\s+(cbuffer|sampler|texture|UAV)\s+\S+\s+(\S+)\s+((?:CB|T|U|S)\d+)\s+((?:cb|t|u|s)\d+(?:,space\d+)?)(unbounded|\s+\d+)\s*$/,
    );
    if (resource)
      resources.push({
        name: resource[1],
        kind: resource[2],
        dimension: resource[3],
        id: resource[4],
        binding: resource[5],
        register: Number(resource[5].match(/\d+/)?.[0]),
        space: Number(resource[5].match(/space(\d+)/)?.[1] ?? 0),
        count: resource[6].trim(),
      });
    if (line === "; Input signature:" && !signaturesDone) section = "input";
    if (line === "; Output signature:" && !signaturesDone) section = "output";
    if (line.startsWith("; Pipeline Runtime Information")) {
      signaturesDone = true;
      section = "";
    }
    const sig = line.match(/^;\s*(\w+)\s+(\d+)\s+([xyzw]+)\s+(\d+)\s+/);
    if (section && sig)
      (section === "input" ? input : output).push(sig[1] + sig[2]);
  }
  // Signature IDs are not physical register numbers. Prefer the actual DXIL entry metadata.
  const metadata = new Map<string, string[]>();
  for (const line of lines) {
    const m = line.match(/^!(\d+)\s*=\s*!\{(.*)\}$/);
    if (m) metadata.set("!" + m[1], splitOperands(m[2]));
  }
  const entryRef = lines
    .find((l) => l.startsWith("!dx.entryPoints"))
    ?.match(/!\{(!\d+)/)?.[1];
  const signature = metadata.get(metadata.get(entryRef ?? "")?.[2] ?? "");
  if (signature)
    for (const [position, target] of [
      [0, input],
      [1, output],
    ] as const) {
      const members = metadata.get(signature[position]);
      if (!members) continue;
      for (const ref of members) {
        const entry = metadata.get(ref),
          id = Number(operand(entry?.[0] ?? ""));
        const name = entry?.[1].match(/^!"([^"]+)"$/)?.[1];
        const index = operand(metadata.get(entry?.[4] ?? "")?.[0] ?? "i32 0");
        if (Number.isInteger(id) && name) target[id] = name + index;
      }
    }
  return { resources, fields, input, output };
}
function parseFunctions(lines: string[]): FunctionBody[] {
  const functions: FunctionBody[] = [];
  let current: FunctionBody | undefined,
    block = "%0";
  for (let i = 0; i < lines.length; i++) {
    const text = lines[i].trim();
    const definition = text.match(
      /^define\s+(.+?)\s+@("[^"]+"|[^\s(]+)\((.*)\).*\{$/,
    );
    if (definition) {
      const params = splitOperands(definition[3])
        .map((p) => {
          const [type, value] = typed(p);
          return scalarType(type) + " " + variable(value);
        })
        .join(", ");
      current = {
        name: identifier(definition[2]),
        start: i + 1,
        end: i + 1,
        signature:
          scalarType(definition[1]) +
          " " +
          identifier(definition[2]) +
          "(" +
          params +
          ")",
        nodes: [],
      };
      functions.push(current);
      block = "%0";
      continue;
    }
    if (!current) continue;
    if (text === "}") {
      current.end = i + 1;
      current = undefined;
      continue;
    }
    const label =
      text.match(/^; <label>:(\d+)/) ??
      text.match(/^("[^"]+"|[\w.$-]+):(?:\s*;.*)?$/);
    if (label) {
      block = "%" + label[1];
      current.nodes.push({
        id: "",
        body: "",
        comment: "",
        line: i + 1,
        block,
        label: block,
        expression: blockName(block) + ":",
        type: "void",
        category: "translated",
        pure: false,
        alias: false,
        sources: [i + 1],
        refs: [],
      });
      continue;
    }
    if (!text || text.startsWith(";")) continue;
    const [code, ...comment] = text.split(/\s+;/);
    const assignment = code.match(/^(%(?:"[^"]+"|[\w.$-]+))\s*=\s*(.*)$/);
    current.nodes.push({
      id: assignment?.[1] ?? "",
      body: assignment?.[2] ?? code,
      comment: comment.join(";"),
      line: i + 1,
      block,
      expression: "",
      type: "auto",
      category: "translated",
      pure: false,
      alias: false,
      sources: [i + 1],
      refs: [],
    });
  }
  return functions;
}
// Exact Microsoft DxilConstants.h OpCode values. Exp/Log are base 2; FMad is not Fma.
const unary: Record<number, string> = {
  6: "abs",
  7: "saturate",
  8: "isnan",
  9: "isinf",
  10: "isfinite",
  11: "isnormal",
  12: "cos",
  13: "sin",
  14: "tan",
  15: "acos",
  16: "asin",
  17: "atan",
  18: "cosh",
  19: "sinh",
  20: "tanh",
  21: "exp2",
  22: "frac",
  23: "log2",
  24: "sqrt",
  25: "rsqrt",
  26: "round_even",
  27: "floor",
  28: "ceil",
  29: "trunc",
  30: "reversebits",
  31: "countbits",
  32: "firstbitlow",
  33: "firstbithigh",
  34: "firstbithigh_signed",
  83: "ddx_coarse",
  84: "ddy_coarse",
  85: "ddx_fine",
  86: "ddy_fine",
};
const binary: Record<number, string> = {
  35: "max",
  36: "min",
  37: "max_signed",
  38: "min_signed",
  39: "max",
  40: "min",
};
const classPrefix = ["T", "U", "CB", "S"];
const binaryOps: Record<string, string> = {
  fadd: "+",
  fsub: "-",
  fmul: "*",
  fdiv: "/",
  frem: "%",
  add: "+",
  sub: "-",
  mul: "*",
  udiv: "/",
  sdiv: "/",
  urem: "%",
  srem: "%",
  shl: "<<",
  lshr: ">>",
  ashr: ">>",
  and: "&",
  or: "|",
  xor: "^",
};
function fieldComponent(field: BufferField, byte: number): string | undefined {
  const shape = field.type.match(
    /^(float|half|double|int|uint|bool)([1-4])?(?:x([1-4]))?$/,
  );
  if (!shape || ["double", "half", "bool"].includes(shape[1])) return;
  const rows = Number(shape[2] ?? 1),
    columns = Number(shape[3] ?? 0),
    matrix = columns > 0;
  const stride = matrix
    ? 16 * (field.major === "row_major" ? rows : columns)
    : field.count > 1
      ? 16
      : rows * 4;
  const relative = byte - field.offset;
  if (relative < 0 || relative % 4 || relative >= stride * field.count) return;
  const index = Math.floor(relative / stride),
    within = relative % stride;
  let path =
    field.buffer +
    "." +
    field.name +
    (field.count > 1 ? "[" + index + "]" : "");
  if (matrix) {
    const major = Math.floor(within / 16),
      minor = (within % 16) / 4;
    if (minor >= (field.major === "row_major" ? columns : rows)) return;
    path +=
      field.major === "row_major"
        ? "[" + major + "][" + minor + "]"
        : "[" + minor + "][" + major + "]";
  } else {
    if (within >= rows * 4) return;
    if (rows > 1) path += "." + "xyzw"[within / 4];
  }
  return path;
}

export function convertDxil(
  ir: string,
  options: { foldExpressions?: boolean } = {},
): ReadableShader {
  const lines = ir.split(/\r?\n/),
    reflection = parseReflection(lines),
    functions = parseFunctions(lines);
  const result: ReadableShader = {
    code: "",
    sourceMap: [],
    resources: reflection.resources,
    fields: reflection.fields,
    stats: {
      instructions: 0,
      translated: 0,
      namedDxCalls: 0,
      raw: 0,
      folded: 0,
      blocks: 0,
    },
    warnings: [],
  };
  const output: string[] = [];
  const emit = (text: string, sources: number[] = []) => {
    output.push(text);
    result.sourceMap.push([...new Set(sources)]);
  };
  const statement = (text: string, sources: number[], raw: boolean) => {
    // Sampling and named DX calls otherwise become wider than an editor pane.
    // Each visual line still maps to the same original instruction and dependencies.
    const call = raw ? null : text.match(/(?:\.Sample\w*|dx\.\w+)\(/);
    if (text.length <= 110 || !call) {
      emit(text, sources);
      return;
    }
    const start = call.index! + call[0].length;
    let depth = 1,
      end = start;
    for (; end < text.length; end++) {
      if (text[end] === "(") depth++;
      if (text[end] === ")" && --depth === 0) break;
    }
    const args = splitOperands(text.slice(start, end));
    if (args.length < 3 || depth !== 0) {
      emit(text, sources);
      return;
    }
    emit(text.slice(0, start), sources);
    for (let i = 0; i < args.length; i++)
      emit("      " + args[i] + (i < args.length - 1 ? "," : ""), sources);
    emit("  " + text.slice(end), sources);
  };
  emit("// DXIL 可读伪代码：用于阅读，不是原始 HLSL，也不能直接编译。");
  emit(
    "// phi_from 按前驱块选择值；标签/goto 保留控制流，不强行猜测高级循环。",
  );
  emit(
    "// dx.* 保留尚未展开的 DXIL 调用；llvm_raw 保留未知指令。点击代码行可追溯 IR。",
  );
  emit("");
  if (!functions.length)
    result.warnings.push("未找到 DXIL 函数定义，请查看原始 IR。");
  for (const fn of functions) {
    const definitions = new Map(
      fn.nodes.filter((n) => n.id).map((n) => [n.id, n]),
    );
    const handles = new Map<string, ShaderResource>(),
      cbReads = new Map<string, CBufferRead>();
    const findResource = (cls: number, range: number) =>
      reflection.resources.find((r) => r.id === classPrefix[cls] + range);
    const callNode = (n: Instruction) => {
      const call = n.body.match(
        /^(?:tail\s+)?call\s+(.+?)\s+@([\w.$-]+)\((.*)\)(?:\s+.*)?$/,
      );
      if (!call) return false;
      n.type = scalarType(call[1]);
      const args = splitOperands(call[3]),
        values = args.map(operand);
      const name = call[2]
        .replace(/^dx\.op\./, "")
        .replace(/\.(?:f16|f32|f64|i16|i32|i64)$/, "");
      if (!call[2].startsWith("dx.op.")) {
        n.expression = identifier(call[2]) + "(" + values.join(", ") + ")";
        return true;
      }
      const opcode = Number(values[0]),
        a = values.slice(1);
      if (name === "createHandle" || name === "createHandleFromBinding") {
        let resource: ShaderResource | undefined;
        if (name === "createHandle")
          resource = findResource(Number(a[0]), Number(a[1]));
        else {
          const binding =
            a[0] === "zeroinitializer"
              ? [0, 0, 0, 0]
              : splitOperands(a[0].replace(/^\{|\}$/g, "")).map((p) =>
                  Number(operand(p)),
                );
          resource = reflection.resources.find(
            (r) =>
              r.id.startsWith(classPrefix[binding[3]] ?? "!") &&
              r.register === binding[0] &&
              r.space === binding[2],
          );
        }
        if (resource) {
          handles.set(n.id, resource);
          n.expression =
            resource.count === "1"
              ? resource.name
              : resource.name +
                ".descriptor(" +
                (name === "createHandle" ? a[2] : a[1]) +
                ", non_uniform: " +
                (name === "createHandle" ? a[3] : a[2]) +
                ")";
          n.alias = true;
          return true;
        }
      }
      if (name === "annotateHandle") {
        const resource = handles.get(a[0]);
        if (resource) handles.set(n.id, resource);
        n.expression = a[0];
        n.alias = true;
        return true;
      }
      if (name === "createHandleFromHeap") {
        n.expression =
          (a[1] === "true"
            ? "SamplerDescriptorHeap"
            : "ResourceDescriptorHeap") +
          "[" +
          a[0] +
          "] /* non_uniform: " +
          a[2] +
          " */";
        n.alias = true;
        return true;
      }
      if (name === "cbufferLoadLegacy") {
        n.expression = "cbuffer_read4(" + a[0] + ", register: " + a[1] + ")";
        n.type = "CBufferRead";
        n.alias = true;
        cbReads.set(n.id, {
          handle: a[0],
          register: a[1],
          scalar: call[1].endsWith(".f32") ? "float" : "uint",
          sources: [n.line],
        });
        return true;
      }
      if (name === "loadInput" || name === "storeOutput") {
        const load = name === "loadInput",
          signature = (load ? reflection.input : reflection.output)[
            Number(a[0])
          ];
        const component =
          /^\d+$/.test(a[2]) && Number(a[2]) < 4
            ? "." + "xyzw"[Number(a[2])]
            : "[" + a[2] + "]";
        const base = signature ? "." + signature : ".signature[" + a[0] + "]";
        const access =
          (load ? "input" : "output") +
          base +
          (a[1] === "0" ? "" : "[" + a[1] + "]") +
          component;
        n.expression = load
          ? access +
            (a[3] && a[3] !== "undefined_value"
              ? " /* vertex: " + a[3] + " */"
              : "")
          : access + " = " + a[3];
        return true;
      }
      if (unary[opcode] && ["unary", "isSpecialFloat"].includes(name)) {
        n.expression = unary[opcode] + "(" + a[0] + ")";
        n.pure = opcode < 83;
        return true;
      }
      if (binary[opcode] && name === "binary") {
        n.expression = binary[opcode] + "(" + a.join(", ") + ")";
        n.pure = true;
        return true;
      }
      if (name === "tertiary" && [46, 47, 48, 49].includes(opcode)) {
        const opNames: Record<number, string> = {
          46: "mad",
          47: "fma",
          48: "mad_signed",
          49: "mad_uint",
        };
        n.expression = opNames[opcode] + "(" + a.join(", ") + ")";
        n.pure = true;
        return true;
      }
      if (/^dot[234]$/.test(name)) {
        const count = Number(name.at(-1));
        n.expression =
          "dot(float" +
          count +
          "(" +
          a.slice(0, count).join(", ") +
          "), float" +
          count +
          "(" +
          a.slice(count).join(", ") +
          "))";
        n.pure = true;
        return true;
      }
      if (["threadId", "groupId", "threadIdInGroup"].includes(name)) {
        const semantics: Record<string, string> = {
          threadId: "SV_DispatchThreadID",
          groupId: "SV_GroupID",
          threadIdInGroup: "SV_GroupThreadID",
        };
        n.expression =
          semantics[name] +
          (Number(a[0]) < 3 ? "." + "xyz"[Number(a[0])] : "[" + a[0] + "]");
        return true;
      }
      if (name === "flattenedThreadIdInGroup") {
        n.expression = "SV_GroupIndex";
        return true;
      }
      if (name === "sampleIndex") {
        n.expression = "SV_SampleIndex";
        return true;
      }
      if (name === "discard") {
        n.expression = "if (" + a[0] + ") discard";
        return true;
      }
      if (["legacyF16ToF32", "legacyF32ToF16"].includes(name)) {
        n.expression =
          (name === "legacyF16ToF32" ? "f16tof32" : "f32tof16") +
          "(" +
          a[0] +
          ")";
        n.pure = true;
        return true;
      }
      if (["waveReadLaneFirst", "waveAllTrue"].includes(name)) {
        n.expression =
          (name === "waveAllTrue" ? "WaveActiveAllTrue" : "WaveReadLaneFirst") +
          "(" +
          a.join(", ") +
          ")";
        return true;
      }
      if (
        ["sample", "sampleGrad", "sampleLevel", "sampleBias"].includes(name)
      ) {
        const resource = handles.get(a[0]),
          dim = resource?.dimension.toLowerCase() ?? "";
        const dims: Record<string, number> = {
          "1d": 1,
          "1darray": 2,
          "2d": 2,
          "2darray": 3,
          "3d": 3,
          cube: 3,
          cubearray: 4,
        };
        const count = dims[dim];
        // Heap descriptors with unknown dimensions retain the complete named DXIL call.
        if (count) {
          const coords = a.slice(2, 2 + count),
            coord =
              count === 1
                ? coords[0]
                : "float" + count + "(" + coords.join(", ") + ")";
          const offsetCount = dim.startsWith("1d")
            ? 1
            : dim.includes("cube") || dim === "3d"
              ? 3
              : 2;
          const offset =
            offsetCount === 1
              ? a[6]
              : "int" +
                offsetCount +
                "(" +
                a.slice(6, 6 + offsetCount).join(", ") +
                ")";
          let suffix = "";
          if (name === "sampleGrad") {
            const g = dim.includes("array") ? count - 1 : count;
            const vector = (start: number) =>
              g === 1
                ? a[start]
                : "float" +
                  g +
                  "(" +
                  a.slice(start, start + g).join(", ") +
                  ")";
            suffix =
              ", ddx: " +
              vector(9) +
              ", ddy: " +
              vector(12) +
              ", clamp: " +
              a[15];
          } else if (name === "sampleLevel" || name === "sampleBias") {
            suffix =
              ", " + (name === "sampleLevel" ? "lod" : "bias") + ": " + a[9];
          } else suffix = ", clamp: " + a[9];
          const methods: Record<string, string> = {
            sample: "Sample",
            sampleGrad: "SampleGrad",
            sampleLevel: "SampleLevel",
            sampleBias: "SampleBias",
          };
          n.expression =
            "(" +
            a[0] +
            ")." +
            methods[name] +
            "(" +
            a[1] +
            ", " +
            coord +
            ", offset: " +
            offset +
            suffix +
            ")";
          n.type = "ResourceRead";
          return true;
        }
      }
      const annotation = n.comment.match(/\w+\(([^)]*)\)/)?.[1],
        labels = annotation ? splitOperands(annotation) : [];
      n.expression =
        "dx." +
        name +
        "(" +
        (["unary", "binary", "tertiary", "isSpecialFloat"].includes(name)
          ? "opcode: " + opcode + ", "
          : "") +
        a.map((v, i) => (labels[i] ? labels[i] + ": " + v : v)).join(", ") +
        ")";
      n.category = "named";
      return true;
    };
    for (const n of fn.nodes) {
      if (n.label) {
        result.stats.blocks++;
        continue;
      }
      result.stats.instructions++;
      const op = n.body.split(/\s+/)[0];
      let matched = false;
      if (op === "call" || op === "tail") matched = callNode(n);
      else if (binaryOps[op]) {
        const rest = n.body
          .slice(op.length)
          .trim()
          .replace(
            /^(?:(?:fast|nnan|ninf|nsz|arcp|contract|afn|reassoc|nuw|nsw|exact)\s+)*/,
            "",
          );
        const [type, value] = typed(rest),
          args = splitOperands(value).map((v) => constant(v, type));
        if (args.length === 2) {
          const signed = type === "i32" ? "int" : "int" + type.slice(1) + "_t";
          n.type = scalarType(type);
          n.expression =
            "(" +
            (["sdiv", "srem", "ashr"].includes(op)
              ? signed + "(" + args[0] + ")"
              : args[0]) +
            " " +
            binaryOps[op] +
            " " +
            (["sdiv", "srem"].includes(op)
              ? signed + "(" + args[1] + ")"
              : args[1]) +
            ")";
          n.pure = true;
          matched = true;
        }
      } else if (op === "fneg") {
        const [type, value] = typed(n.body.slice(4).trim());
        n.type = scalarType(type);
        n.expression = "(-" + constant(value, type) + ")";
        n.pure = true;
        matched = true;
      } else if (["icmp", "fcmp"].includes(op)) {
        const compare = n.body.match(
          /^(?:icmp|fcmp)\s+(?:(?:fast|nnan|ninf|nsz|arcp|contract|afn|reassoc)\s+)*(\w+)\s+(.*)$/,
        );
        if (compare) {
          const [type, value] = typed(compare[2]),
            args = splitOperands(value).map((v) => constant(v, type)),
            predicate = compare[1];
          const symbols: Record<string, string> = {
            eq: "==",
            ne: "!=",
            gt: ">",
            ge: ">=",
            lt: "<",
            le: "<=",
          };
          const symbol =
            symbols[predicate.replace(/^[sou](?=eq|ne|gt|ge|lt|le)/, "")];
          if (
            args.length === 2 &&
            (symbol || ["ord", "uno", "true", "false"].includes(predicate))
          ) {
            const [a, b] = args;
            n.type = "bool";
            let expr = "(" + a + " " + symbol + " " + b + ")";
            if (op === "icmp" && /^[su]/.test(predicate)) {
              const cast =
                predicate[0] === "s"
                  ? type === "i32"
                    ? "int"
                    : "int" + type.slice(1) + "_t"
                  : scalarType(type);
              expr =
                "(" +
                cast +
                "(" +
                a +
                ") " +
                symbol +
                " " +
                cast +
                "(" +
                b +
                "))";
            }
            if (
              op === "fcmp" &&
              predicate.startsWith("u") &&
              predicate !== "uno"
            )
              expr = "(isnan(" + a + ") || isnan(" + b + ") || " + expr + ")";
            if (op === "fcmp" && predicate === "one")
              expr = "(!isnan(" + a + ") && !isnan(" + b + ") && " + expr + ")";
            if (predicate === "ord")
              expr = "(!isnan(" + a + ") && !isnan(" + b + "))";
            if (predicate === "uno")
              expr = "(isnan(" + a + ") || isnan(" + b + "))";
            if (["true", "false"].includes(predicate)) expr = predicate;
            n.expression = expr;
            n.pure = true;
            matched = true;
          }
        }
      } else if (op === "select") {
        const args = splitOperands(n.body.slice(6).trim());
        if (args.length === 3) {
          n.type = scalarType(typed(args[1])[0]);
          n.expression =
            "(" +
            operand(args[0]) +
            " ? " +
            operand(args[1]) +
            " : " +
            operand(args[2]) +
            ")";
          n.pure = true;
          matched = true;
        }
      } else if (op === "extractvalue") {
        const args = splitOperands(n.body.slice(12).trim()),
          aggregate = operand(args[0]),
          component = Number(args[1]),
          read = cbReads.get(aggregate);
        if (args.length === 2 && Number.isInteger(component)) {
          const suffix =
            typed(args[0])[0].match(/\.(f16|f32|f64|i16|i32|i64)$/)?.[1] ??
            "i32";
          n.type = (
            {
              f16: "half",
              f32: "float",
              f64: "double",
              i16: "uint16_t",
              i32: "uint",
              i64: "uint64_t",
            } as Record<string, string>
          )[suffix];
          n.expression =
            "(" +
            aggregate +
            ")." +
            (component < 4
              ? "xyzw"[component]
              : component === 4
                ? "status"
                : "field_" + component);
          n.pure = true;
          matched = true;
          if (read) {
            const resource = handles.get(read.handle);
            if (resource && /^\d+$/.test(read.register)) {
              const byte = Number(read.register) * 16 + component * 4;
              const field = reflection.fields.find(
                (f) => f.buffer === resource.name && fieldComponent(f, byte),
              );
              if (field) {
                const path = fieldComponent(field, byte)!,
                  kind = field.type.match(/^[a-z]+/)?.[0];
                n.expression =
                  read.scalar === "float" && kind !== "float"
                    ? "asfloat(" + path + ")"
                    : read.scalar === "uint" &&
                        ["float", "int"].includes(kind ?? "")
                      ? "asuint(" + path + ")"
                      : path;
                n.sources.push(
                  ...read.sources,
                  ...(definitions.get(read.handle)?.sources ?? []),
                );
              }
            }
          }
        }
      } else if (
        [
          "bitcast",
          "uitofp",
          "sitofp",
          "fptoui",
          "fptosi",
          "fptrunc",
          "fpext",
          "zext",
          "sext",
          "trunc",
        ].includes(op)
      ) {
        const cast = n.body
          .slice(op.length)
          .trim()
          .match(/^(.+)\s+to\s+(.+)$/);
        if (cast) {
          const [from, value] = typed(cast[1]),
            to = cast[2],
            v = constant(value, from);
          const signedFrom =
            from === "i32" ? "int" : "int" + from.slice(1) + "_t";
          const signedTo = to === "i32" ? "int" : "int" + to.slice(1) + "_t";
          n.type = scalarType(to);
          n.expression =
            op === "bitcast"
              ? from === "float" && to === "i32"
                ? "asuint(" + v + ")"
                : from === "i32" && to === "float"
                  ? "asfloat(" + v + ")"
                  : "bitcast<" + n.type + ">(" + v + ")"
              : n.type +
                "(" +
                (op === "sitofp" || op === "sext"
                  ? signedFrom + "(" + v + ")"
                  : op === "fptosi"
                    ? signedTo + "(" + v + ")"
                    : v) +
                ")";
          n.pure = true;
          matched = true;
        }
      } else if (op === "phi") {
        const [type, value] = typed(n.body.slice(3).trim()),
          pairs = splitOperands(value);
        n.type = scalarType(type);
        if (pairs.length && pairs.every((p) => /^\[.*\]$/.test(p))) {
          n.expression =
            "phi_from(" +
            pairs
              .map((p) => {
                const [v, label] = splitOperands(p.slice(1, -1));
                return blockName(label) + ": " + constant(v, type);
              })
              .join(", ") +
            ")";
          matched = true;
        }
      } else if (op === "br") {
        const args = splitOperands(n.body.slice(2).trim()).filter(
          (a) => !a.startsWith("!"),
        );
        if (args.length === 1) {
          n.expression = "goto " + blockName(operand(args[0]));
          matched = true;
        }
        if (args.length === 3) {
          n.expression =
            "if (" +
            operand(args[0]) +
            ") goto " +
            blockName(operand(args[1])) +
            "; else goto " +
            blockName(operand(args[2]));
          matched = true;
        }
        n.type = "void";
      } else if (op === "ret") {
        n.type = "void";
        n.expression =
          n.body === "ret void"
            ? "return"
            : "return " + operand(n.body.slice(3).trim());
        matched = true;
      } else if (op === "load") {
        const args = splitOperands(n.body.slice(4).trim());
        n.type = scalarType(args[0]);
        n.expression = "*" + operand(args[1]);
        matched = args.length >= 2 && !/\b(atomic|volatile)\b/.test(n.body);
      } else if (op === "store") {
        const args = splitOperands(n.body.slice(5).trim());
        n.type = "void";
        n.expression = "*" + operand(args[1]) + " = " + operand(args[0]);
        matched = args.length >= 2 && !/\b(atomic|volatile)\b/.test(n.body);
      } else if (op === "alloca") {
        const args = splitOperands(n.body.slice(6).trim());
        n.type = "ptr<" + scalarType(args[0]) + ">";
        n.expression =
          "stack_alloc<" +
          scalarType(args[0]) +
          ">(" +
          (args[1]?.startsWith("i") ? operand(args[1]) : "1") +
          ")";
        matched = true;
      } else if (op === "getelementptr") {
        const args = splitOperands(
          n.body
            .slice(13)
            .trim()
            .replace(/^inbounds\s+/, ""),
        );
        n.type = "ptr<auto>";
        n.expression =
          "gep_ptr(" +
          operand(args[1]) +
          ", " +
          args.slice(2).map(operand).join(", ") +
          ")";
        matched = args.length >= 3;
      }
      if (!matched) {
        n.expression = "llvm_raw(" + JSON.stringify(n.body) + ")";
        n.category = "raw";
        n.pure = false;
        n.alias = false;
        result.warnings.push("IR " + n.line + ": 未识别指令 " + op);
      }
      n.refs = [
        ...(n.category === "raw" ? n.body : n.expression).matchAll(ssaPattern),
      ]
        .map((m) => m[0])
        .filter((id) => definitions.has(id));
      if (n.category === "raw") result.stats.raw++;
      else if (n.category === "named") result.stats.namedDxCalls++;
      else result.stats.translated++;
    }
    const consumers = new Map<string, Instruction[]>();
    for (const n of fn.nodes)
      for (const ref of n.refs) {
        const list = consumers.get(ref) ?? [];
        list.push(n);
        consumers.set(ref, list);
      }
    const eligible = (definition: Instruction, consumer: Instruction) =>
      definition.alias ||
      (options.foldExpressions !== false &&
        definition.pure &&
        consumers.get(definition.id)?.length === 1 &&
        definition.block === consumer.block &&
        !consumer.body.startsWith("phi ") &&
        definition.expression.length <= 100);
    const render = (
      n: Instruction,
      depth = 0,
      stack = new Set<string>(),
    ): { text: string; sources: number[]; kept: Set<string> } => {
      const sources = [...n.sources];
      const kept = new Set<string>();
      if (n.category === "raw")
        return { text: n.expression, sources, kept: new Set(n.refs) };
      const text = n.expression.replace(ssaPattern, (ref) => {
        const definition = definitions.get(ref);
        if (!definition) return variable(ref);
        if (stack.has(ref) || (depth >= 4 && !definition.alias)) {
          kept.add(ref);
          return variable(ref);
        }
        if (!eligible(definition, n)) {
          kept.add(ref);
          return variable(ref);
        }
        const nested = render(definition, depth + 1, new Set([...stack, ref]));
        if (nested.text.length > 180 && !definition.alias) {
          kept.add(ref);
          return variable(ref);
        }
        for (const id of nested.kept) kept.add(id);
        sources.push(...nested.sources);
        return nested.text;
      });
      return { text, sources, kept };
    };
    // Emit effectful roots and their remaining dependencies, including references left by
    // the depth/length budget. A preview rendering must never delete a needed definition.
    const needed = new Set<Instruction>();
    const queue: Instruction[] = [];
    for (const n of fn.nodes) {
      if (n.label) continue;
      const uses = consumers.get(n.id) ?? [];
      if (
        !n.id ||
        (!n.alias &&
          (!uses.length || uses.length !== 1 || !eligible(n, uses[0])))
      ) {
        needed.add(n);
        queue.push(n);
      }
    }
    const rendered = new Map<Instruction, ReturnType<typeof render>>();
    for (let i = 0; i < queue.length; i++) {
      const n = queue[i],
        r = render(n);
      rendered.set(n, r);
      for (const id of r.kept) {
        const dependency = definitions.get(id);
        if (dependency && !needed.has(dependency)) {
          needed.add(dependency);
          queue.push(dependency);
        }
      }
    }
    emit(fn.signature + " {", [fn.start]);
    if (!fn.nodes[0]?.label) result.stats.blocks++;
    if (!fn.nodes[0]?.label)
      emit("  " + blockName(fn.nodes[0]?.block ?? "%0") + ":", [fn.start]);
    for (const n of fn.nodes) {
      if (n.label) {
        emit("");
        emit("  " + n.expression, n.sources);
        continue;
      }
      if (!needed.has(n)) {
        result.stats.folded++;
        continue;
      }
      const r = rendered.get(n)!;
      statement(
        "  " +
          (n.id ? n.type + " " + variable(n.id) + " = " : "") +
          r.text +
          ";",
        r.sources,
        n.category === "raw",
      );
    }
    emit("}", [fn.end]);
    emit("");
  }
  result.code = output.join("\n");
  return result;
}
