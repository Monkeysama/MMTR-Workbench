import type { MdfProperty } from "../types";

/** Display conventions used by RE Mesh; MDF still stores float32 values. */
export function propertyControl(property: Pick<MdfProperty, "name" | "count">) {
  const name = property.name;
  const lower = name.toLowerCase();
  if (property.count === 4 && (lower.includes("color") || lower.includes("_col_")) && !lower.includes("rate")) return "color";
  if (property.count === 1 && (name.includes("Use") || name.includes("_or_") || name.startsWith("is") || ["BackFaceNormalFilp", "uv1or2_AlphaMap"].includes(name))) return "boolean";
  return property.count > 1 ? "vector" : "float";
}

export function linearToSrgb(value: number) {
  return value <= .0031308 ? 12.92 * value : 1.055 * value ** (1 / 2.4) - .055;
}
export function srgbToLinear(value: number) {
  return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
}
export function colorHex(values: number[]) {
  return "#" + values.slice(0, 3).map(value => Math.round(Math.max(0, Math.min(1, linearToSrgb(value))) * 255).toString(16).padStart(2, "0")).join("");
}
export function colorComponents(hex: string): Record<number, number> | null {
  if (!/^#[0-9a-f]{6}$/i.test(hex)) return null;
  return Object.fromEntries([0, 1, 2].map(index => [index, srgbToLinear(parseInt(hex.slice(1 + index * 2, 3 + index * 2), 16) / 255)]));
}
