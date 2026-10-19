"""Directional audit of explicit DXIL precise-call constraints.

This is separate from the restricted value/effect comparator, whose optimizer
flag normalization is not by itself evidence that precise markers survived.
"""
import re
from .verify_ir import Graph, compare_dataflow
from .control_flow import NestedLoopRequired
from .reconstruct import ReconstructionError


def compare_precision_constraints(source_ir, candidate_ir, *, dataflow=None):
    flow = compare_dataflow(source_ir, candidate_ir) if dataflow is None else dataflow
    if not flow['equal']:
        raise ReconstructionError('精度约束核对需要先通过受限数据流比较。')
    try:
        left, right = Graph(source_ir), Graph(candidate_ir)
    except NestedLoopRequired:
        left, right = Graph(source_ir, nested_compute=True), Graph(candidate_ir, nested_compute=True)
        mapping = flow['state_mapping']
        keys = {loop['key']: tuple(next(s['key'][2] for s in left.nested_loops[loop['key']]['states']
                    if s['name'] == next(m['source'] for m in mapping if m['candidate'] == state['name']))
                    for state in loop['states']) for loop in right.nested_loops}
        right = Graph(candidate_ir, nested_compute=True, nested_state_keys=keys)
    else:
        mapping = flow.get('compute_loop_state_mapping')
        if mapping:
            keys = tuple(next(s['key'][1] for s in left.loop_states
                    if s['name'] == next(m['source'] for m in mapping if m['candidate'] == state['name']))
                    for state in right.loop_states)
            right = Graph(candidate_ir, compute_state_keys=keys)
    # Install any proven induction facts and event contexts exactly as used
    # by the dataflow comparator before computing expression/guard identities.
    left.observables(); right.observables()
    def constraints(graph):
        result = {}
        for name in graph.precise_results:
            expression = graph.expression(name)
            block = graph.defs[name][0]
            guard = graph.reach(block)
            result[expression] = graph.logic('or', result.get(expression, '0'), guard)
        return result
    required, actual = constraints(left), constraints(right)
    def floating_flags(graph, *, candidate=False):
        pending=list(graph.precise_results);seen=set();result={}
        while pending:
            name=pending.pop()
            if name in seen or name not in graph.defs:continue
            seen.add(name);block,body=graph.defs[name]
            pending.extend(re.findall(r'%[\w.]+',body))
            m=re.match(r'(fadd|fsub|fmul|fdiv|frem) ((?:(?:fast|reassoc|nnan|ninf|nsz|arcp|contract|afn) )*)(float|half) ',body)
            if m:
                expression=graph.expression(name);flags=set(m[2].split())
                if expression in result:
                    # Require the strongest source constraint and reject any
                    # weaker candidate occurrence of the shared expression.
                    if candidate:result[expression]|=flags
                    else:result[expression]&=flags
                else:result[expression]=flags
        return result
    source_flags, candidate_flags=floating_flags(left),floating_flags(right,candidate=True)
    weakened_flags=[dict(expression=expression,source=sorted(flags),candidate=sorted(candidate_flags[expression]))
        for expression,flags in sorted(source_flags.items())
        if expression in candidate_flags and not candidate_flags[expression]<=flags]
    left.nodes.update(right.nodes);left.bdd_nodes.update(right.bdd_nodes)
    missing = []
    for expression, guard in sorted(required.items()):
        uncovered = left.logic('and', guard, left.logic('xor', actual.get(expression, '0'), '1'))
        if uncovered != '0':
            missing.append(dict(expression=expression, uncovered_guard=uncovered))
    return dict(preserved=not missing and not weakened_flags, source_tagged_calls=len(left.precise_results),
                candidate_tagged_calls=len(right.precise_results), required_expressions=len(required), missing=missing,
                floating_dependencies_checked=len(source_flags),weakened_flags=weakened_flags,
                limitation='Supported explicit floating DXIL calls and exact execution guards; not a general IEEE proof.')
