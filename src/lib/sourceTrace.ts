import type { ReconstructionTrace, SourceInput } from "../types.ts";

export function traceIsCurrent(trace: ReconstructionTrace | null, source: SourceInput): boolean {
  return !!trace && trace.baseline.source === source.source &&
    trace.baseline.entry === source.entry && trace.baseline.profile === source.profile;
}

export function originalLinesAt(trace: ReconstructionTrace | null, source: SourceInput, line: number): number[] {
  if (!traceIsCurrent(trace, source) || !trace || !Number.isInteger(line) || line < 1) return [];
  const limit = trace.ir.split("\n").length;
  return [...new Set((trace.sourceMap[line - 1] ?? []).filter(
    n => Number.isInteger(n) && n >= 1 && n <= limit,
  ))].sort((a, b) => a - b);
}
