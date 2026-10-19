"""Compile the supported candidate slice and exercise strict MMTR replacement."""
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mmtr_editor.reconstruct import reconstruct_hlsl, ReconstructionError, function_body
from mmtr_editor.dxc import Dxc, reflection_differences
from mmtr_editor.model import Document, interface_chunks
from inspect_mmtr import sha256
from scripts.verify_volume_blend import canonical_body


def normalized_body(ir):
    _, body = function_body(ir)
    return [re.sub(r'\s+', ' ', line.split(';', 1)[0].strip()) for line in body.splitlines() if line.split(';', 1)[0].strip()]


def main():
    root = Path('output/variation_scan')
    inventory = json.loads((root / 'inventory.json').read_text(encoding='utf-8'))
    results, unsupported = [], []
    dxc = Dxc('C:/Software/Kiro/dxcompiler.dll')
    try:
        for candidate in inventory['candidates']:
            folder = root / 'candidates' / candidate['sha256']
            original_ir = (folder / 'original.ll').read_text(encoding='utf-8')
            try:
                rebuilt = reconstruct_hlsl(original_ir)
            except ReconstructionError as exc:
                unsupported.append({'sha256': candidate['sha256'], 'reason': str(exc)})
                continue
            (folder / 'reconstructed.hlsl').write_text(rebuilt['source'], encoding='utf-8')
            binary, warnings = dxc.compile(rebuilt['source'], rebuilt['entry'], rebuilt['profile'])
            new_ir = dxc.disassemble(binary)
            (folder / 'recompiled.dxbc').write_bytes(binary)
            (folder / 'recompiled.ll').write_text(new_ir, encoding='utf-8')
            original = (folder / 'original.dxbc').read_bytes()
            differences = reflection_differences(original_ir, new_ir)
            item = {'sha256': candidate['sha256'], 'entry': rebuilt['entry'], 'profile': rebuilt['profile'],
                    'instructions': rebuilt['instructions'], 'compiled_size': len(binary), 'warnings': warnings,
                    'interface_equal': interface_chunks(original) == interface_chunks(binary),
                    'reflection_differences': differences, 'body_equal': normalized_body(original_ir) == normalized_body(new_ir),
                    'canonical_body_equal': canonical_body(original_ir) == canonical_body(new_ir),
                    'occurrence_count': len(candidate['occurrences'])}
            results.append(item)
        # A non-empty visibility writer, with strict interface/reflection checks and no expert override.
        proven = next(x for x in results if x['instructions'] == 6 and x['body_equal'] and x['interface_equal'] and not x['reflection_differences'])
        candidate = next(x for x in inventory['candidates'] if x['sha256'] == proven['sha256'])
        occurrence = next((x for x in candidate['occurrences'] if Path(x['source']).name.startswith('Base_ATOS_SkinEdit_VFX.')), candidate['occurrences'][0])
        doc = Document.open(occurrence['source'])
        new_binary = (root / 'candidates' / proven['sha256'] / 'recompiled.dxbc').read_bytes()
        doc.replace_shader(occurrence['index'], new_binary, note={'reflection_differences': [], 'body_equal': True})
        target = root / 'roundtrip' / Path(occurrence['source']).name
        target.parent.mkdir(parents=True, exist_ok=True)
        doc.export(target)
        reread = Document.open(target)
        untouched = all(reread.shader_bytes(i) == doc.original[s['offset']:s['trailer_offset']]
                        for i, s in enumerate(doc.info['shaders']) if i != occurrence['index'])
        roundtrip = {'source': occurrence['source'], 'index': occurrence['index'], 'output': str(target.resolve()),
                     'source_unchanged': sha256(Path(occurrence['source']).read_bytes()) == doc.info['sha256'],
                     'replacement_exact': reread.shader_bytes(occurrence['index']) == new_binary,
                     'other_shaders_unchanged': untouched, 'integrity': reread.integrity,
                     'expert_override': False}
    finally:
        dxc.close()
    report = {'supported': results, 'unsupported': unsupported, 'roundtrip': roundtrip,
              'limitations': ['No game loading/rendering test.', 'Body equality compares emitted instruction text only; interfaces checked separately.',
                             'Current DXC emits a newer validator version than the original game compiler.']}
    (root / 'reconstruction_verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'supported': len(results), 'strict_interface_matches': sum(x['interface_equal'] for x in results),
                      'body_matches': sum(x['body_equal'] for x in results), 'roundtrip': roundtrip}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
