"""Rebuild #24, compare its operation/CFG text and strictly roundtrip the MMTR."""
import json
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inspect_mmtr import sha256
from mmtr_editor.reconstruct import reconstruct_hlsl, function_body
from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.model import Document

SOURCE = Path('F:/MH Wilds/Game Unpack/MHWILDS_EXTRACT/re_chunk_000/natives/STM/MaterialShader/Variation/Simple_VolumeBlend.mmtr.250206176')
DLL = Path('C:/Software/Kiro/dxcompiler.dll')


def canonical_body(ir):
    """Allow only multiply operand commutation and relaxation of a fast fcmp.

    SSA IDs, CFG labels, phi predecessor ordering and all other instructions remain
    exact. Not a general mathematical equivalence checker.
    """
    result = []
    for raw in function_body(ir)[1].splitlines():
        text = raw.split(';', 1)[0].strip()
        if not text:
            label = re.match(r'; <label>:(\d+)', raw)
            if label:
                result.append('block ' + label[1])
            continue
        text = re.sub(r'\s+', ' ', text)
        text = text.replace('fcmp fast ', 'fcmp ')
        m = re.fullmatch(r'(%\d+ = fmul fast float) ([^,]+), (.+)', text)
        if m:
            a, b = sorted([m[2], m[3]])
            text = f'{m[1]} {a}, {b}'
        result.append(text)
    return result


def verify():
    doc = Document.open(SOURCE)
    out = Path('output/volume_blend')
    out.mkdir(parents=True, exist_ok=True)
    dxc = Dxc(DLL)
    try:
        original_ir = dxc.disassemble(doc.shader_bytes(24))
        result = reconstruct_hlsl(original_ir)
        binary, warnings = dxc.compile(result['source'], result['entry'], result['profile'])
        compiled_ir = dxc.disassemble(binary)
    finally:
        dxc.close()
    interface = doc.interface_differences(24, binary)
    reflection = reflection_differences(original_ir, compiled_ir)
    canonical_equal = canonical_body(original_ir) == canonical_body(compiled_ir)
    if interface or reflection or not canonical_equal:
        raise AssertionError((interface, reflection, canonical_equal))
    doc.replace_shader(24, binary, note={'reflection_differences': reflection, 'canonical_body_equal': True})
    destination = out / SOURCE.name
    doc.export(destination)
    reread = Document.open(destination)
    assert reread.shader_bytes(24) == binary
    assert not reread.integrity['name_hash_mismatches'] and not reread.integrity['shader_trailer_mismatches']
    untouched = all(reread.shader_bytes(s['index']) == doc.original[s['offset']:s['trailer_offset']]
                    for s in doc.info['shaders'] if s['index'] != 24)
    assert untouched and sha256(SOURCE.read_bytes()) == doc.info['sha256']
    for name, text in [('0024_original.ll', original_ir), ('0024_recompiled.ll', compiled_ir),
                       ('0024_reconstructed.hlsl', result['source'])]:
        (out / name).write_text(text, encoding='utf-8')
    (out / '0024_recompiled.dxbc').write_bytes(binary)
    report = {'source': str(SOURCE), 'source_sha256': doc.info['sha256'], 'shader': 24,
              'instructions': result['instructions'], 'blocks': result['blocks'], 'warnings': warnings,
              'interface_differences': interface, 'reflection_differences': reflection, 'canonical_body_equal': canonical_equal,
              'expected_differences': ['fmul operand order at SSA 51', 'final fcmp fast flag relaxed at SSA 79'],
              'export': str(destination.resolve()), 'other_shaders_unchanged': untouched, 'integrity': reread.integrity,
              'game_test': 'not_run', 'limitations': ['Textual operation/CFG comparison; no GPU execution or performance proof.']}
    (out / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
