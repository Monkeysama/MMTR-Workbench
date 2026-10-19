/** Compact previews only; editing and exports retain the original float values. */
export function mdfValues(values: number[] | null) {
  return values?.map(value => Number.isFinite(value) ? String(Number(value.toPrecision(7))) : String(value)).join(", ") ?? "未提供";
}

/** Shortest decimal that round-trips to the exact stored float32 value. */
export function mdfEditValues(values: number[]) {
  return values.map(value => {
    if (!Number.isFinite(value)) return String(value);
    if (Object.is(value, -0)) return "-0";
    for (let digits = 1; digits <= 9; digits++) {
      const candidate = Number(value.toPrecision(digits));
      if (Object.is(Math.fround(candidate), value)) return String(candidate);
    }
    return String(value);
  }).join(", ");
}
