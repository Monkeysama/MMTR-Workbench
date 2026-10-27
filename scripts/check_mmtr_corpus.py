"""逐个只读打开真实 MMTR；验证 RPC 快照、原样构建与完整性，报告写在语料目录外。"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mmtr_editor.rpc import Service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    corpus, report = args.root.resolve(), args.report.resolve()
    if not corpus.is_dir() or report.is_relative_to(corpus):
        parser.error('语料必须为目录，报告必须在语料目录外。')
    files = sorted(path for path in corpus.rglob('*')
                   if path.is_file() and re.search(r'\.mmtr(?:\.\d+)?$', path.name, re.I))
    if not files:
        parser.error('没有找到 MMTR 文件。')
    report.parent.mkdir(parents=True, exist_ok=True)
    started, last_progress = time.perf_counter(), 0
    results = []
    # JSONL 保留已完成文件的证据；每次只有一个 Service/Document，不常驻全部语料。
    with report.with_suffix('.jsonl').open('w', encoding='utf-8') as stream:
        for number, path in enumerate(files, 1):
            tick = time.perf_counter()
            row = {'path': str(path.relative_to(corpus))}
            try:
                service = Service()
                snapshot = service.handle({'op': 'open', 'path': str(path)})
                json.dumps(snapshot, allow_nan=False)
                doc = service.doc
                row.update(sha256=doc.info['sha256'], bytes=len(doc.original),
                           groups=doc.info['header_u16_04'], records=doc.info['record_count'],
                           shaders=len(doc.info['shaders']), integrity=doc.integrity,
                           unchanged=doc.build()[0] == doc.original)
                if (not row['unchanged'] or doc.integrity['name_hash_mismatches']
                        or doc.integrity['shader_trailer_mismatches']):
                    raise ValueError('打开成功，但原样构建或完整性校验失败。')
                if (len(snapshot['passes']) != row['records']
                        or {p['group'] for p in snapshot['passes']} != set(range(row['groups']))):
                    raise ValueError('RPC 快照中的记录或组数不完整。')
                row['ok'] = True
                del snapshot, doc, service
            except Exception as exc:
                row.update(ok=False, error=f'{type(exc).__name__}: {exc}')
            row['seconds'] = round(time.perf_counter()-tick, 3)
            results.append(row)
            stream.write(json.dumps(row, ensure_ascii=False)+'\n')
            stream.flush()
            now = time.perf_counter()
            if number == len(files) or number % 10 == 0 or now-last_progress >= 15:
                print(json.dumps({'completed': number, 'total': len(files),
                                  'failed': sum(not r['ok'] for r in results),
                                  'seconds': round(now-started, 1)}, ensure_ascii=True), flush=True)
                last_progress = now
    summary = {'root': str(corpus), 'utc': datetime.now(timezone.utc).isoformat(),
               'total': len(results), 'passed': sum(r['ok'] for r in results),
               'failed': sum(not r['ok'] for r in results),
               'groups': dict(Counter(r.get('groups', 'unknown') for r in results)),
               'bytes': sum(r.get('bytes', 0) for r in results),
               'seconds': round(time.perf_counter()-started, 3),
               'evidence': 'CPU: RPC open, JSON snapshot, unchanged build, name/payload hashes',
               'files': results}
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'files'}, ensure_ascii=True), flush=True)
    return 1 if summary['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
