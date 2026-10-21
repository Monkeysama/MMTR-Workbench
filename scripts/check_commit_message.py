"""校验Conventional Commits标题；本地hook与CI共用，不修改Git历史。"""
import argparse
import json
from pathlib import Path
import re
import subprocess

TYPES = 'feat|fix|docs|refactor|test|build|ci|chore|perf|style|revert'
HEADER = re.compile(rf'^(?:{TYPES})(?:\([a-zA-Z0-9_./-]+\))?!?: [^\s].*$')

# 只校验首行格式；BREAKING CHANGE迁移说明由提交者和review共同检查。
def valid_header(header):
    return bool(HEADER.fullmatch(header))

# push检查本次非merge提交；PR检查标题，以覆盖squash合并后的提交消息。
def event_headers(event):
    if 'pull_request' in event:
        return [event['pull_request']['title']]
    before, after = event.get('before'), event.get('after')
    if not after or set(after) == {'0'}:
        return []
    if before and set(before) != {'0'}:
        revision = [f'{before}..{after}']
    else:
        # 新分支推送没有 before；只检查默认分支之外的提交，不回溯整个历史。
        default = (event.get('repository') or {}).get('default_branch')
        base = f'origin/{default}' if default else None
        exists = base and subprocess.run(['git', 'rev-parse', '--verify', '--quiet', base],
                                         stdout=subprocess.DEVNULL).returncode == 0
        revision = [f'{base}..{after}'] if exists else [after]
    output = subprocess.check_output(['git', 'log', '--no-merges', '--format=%s', *revision], text=True, encoding='utf-8')
    return output.splitlines()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--file', type=Path)
    inputs.add_argument('--event', type=Path)
    args = parser.parse_args()
    if args.file:
        lines = args.file.read_text(encoding='utf-8-sig').splitlines()
        headers = [lines[0] if lines else '']
    else:
        headers = event_headers(json.loads(args.event.read_text(encoding='utf-8')))
    invalid = [header for header in headers if not valid_header(header)]
    if invalid:
        for header in invalid:
            print(f'Invalid commit header: {header}')
        print('Expected type(scope): description; use an ASCII colon, e.g. feat(mdf): 新增贴图槽')
        return 1
    print(f'Conventional Commits: {len(headers)} header(s) passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
