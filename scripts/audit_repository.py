"""Audit the Git index for local assets, generated output and credential tokens.

Reads indexed blobs, not working-tree replacements. Reports only file names and
finding categories, never matched credentials. No files are staged or deleted.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 1024 * 1024
LOCAL_DIRS = {'output', 'node_modules', 'dist', '.build', 'release', '.learnings', '测试截图', '.venv', 'corpus', 'captures'}
LOCAL_FILES = {'SESSION_HANDOFF.md', 'TRANSFER_MANIFEST.json', '简单Shader重建进展.md', '皮肤MMTR对比分析.md'}
ASSET = re.compile(r'\.(?:pak|mmtr|mmrt|mdf2?|tex|mesh|dxbc|dxil|ll|spv|dds|wpix|rdc|bin|exe|dll|pdb|zip|7z)(?:\.\d+)?$', re.I)
SECRETS = [re.compile(rb'gh[pousr]_[A-Za-z0-9]{30,}'),
           re.compile(rb'github_pat_[A-Za-z0-9_]{30,}'),
           re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')]


def path_findings(path):
    p = PurePosixPath(path)
    issues = []
    if p.parts[0] in LOCAL_DIRS or p.name in LOCAL_FILES:
        issues.append('local-only path')
    if p.parts[:2] in [('src-tauri', 'target'), ('src-tauri', 'binaries'), ('src-tauri', 'gen')]:
        issues.append('generated Tauri path')
    if '__pycache__' in p.parts or p.suffix in {'.pyc', '.pyo', '.log', '.tmp', '.bak'}:
        issues.append('cache or temporary file')
    if ASSET.search(path):
        issues.append('game/capture/compiled artifact')
    if p.name == '.env' or (p.name.startswith('.env.') and p.name != '.env.example'):
        issues.append('local environment file')
    return issues


def git(*args, data=None):
    return subprocess.run(['git', *args], cwd=ROOT, input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


def audit():
    entries = []
    for entry in git('ls-files', '--stage', '-z').split(b'\0'):
        if not entry:
            continue
        metadata, path = entry.split(b'\t', 1)
        mode, oid, stage = metadata.split()
        entries.append(dict(path=path.decode('utf-8'), mode=mode.decode(), oid=oid.decode(), stage=stage.decode()))
    findings = []
    if not entries:
        findings.append(dict(path='(index)', reasons=['no indexed files']))
    check = git('cat-file', '--batch-check', data=''.join(e['oid'] + '\n' for e in entries).encode()) if entries else b''
    sizes = {}
    for line in check.splitlines():
        oid, kind, size = line.split()
        sizes[oid.decode()] = (kind.decode(), int(size))
    readable = []
    for e in entries:
        kind, size = sizes[e['oid']]
        e['bytes'] = size
        reasons = path_findings(e['path'])
        if e['mode'] not in {'100644', '100755'} or e['stage'] != '0' or kind != 'blob':
            reasons.append('non-regular or unresolved index entry')
        if size > LIMIT:
            reasons.append('file exceeds 1 MiB review threshold')
        if reasons:
            findings.append(dict(path=e['path'], reasons=reasons))
        elif e['path'] != 'src-tauri/icons/icon.ico':
            readable.append(e)
    payload = git('cat-file', '--batch', data=''.join(e['oid'] + '\n' for e in readable).encode()) if readable else b''
    position = 0
    for e in readable:
        end = payload.index(b'\n', position)
        oid, kind, size = payload[position:end].split()
        size = int(size)
        data = payload[end + 1:end + 1 + size]
        position = end + 2 + size
        if oid.decode() != e['oid'] or len(data) != size:
            raise ValueError('Malformed git cat-file response')
        reasons = []
        if b'\0' in data:
            reasons.append('unreviewed binary content')
        if any(pattern.search(data) for pattern in SECRETS):
            reasons.append('credential-like content (redacted)')
        if reasons:
            findings.append(dict(path=e['path'], reasons=reasons))
    categories = {}
    for e in entries:
        top = e['path'].split('/')[0]
        row = categories.setdefault(top, dict(files=0, bytes=0))
        row['files'] += 1
        row['bytes'] += e['bytes']
    return dict(scope='git index', files=len(entries), bytes=sum(e['bytes'] for e in entries),
                categories=categories, findings=findings, passed=not findings,
                limitations=['High-confidence token/path/size check, not a complete secret or provenance audit.',
                             'Small application icon is an explicit binary exception.',
                             'Does not execute research scripts, upload or delete local files.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staged', action='store_true', help='Audit index (also the default)')
    parser.add_argument('--report', type=Path, help='Optional local JSON result')
    args = parser.parse_args()
    result = audit()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ['scope', 'files', 'bytes', 'findings', 'passed']}, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
