"""Development-only capture from the pinned original tg-cli exports, via Bun."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

SOURCE_REVISION = '7aa28824a1371f95fb34c2dfc699794e6d7d3946'
BUN_REVISION = '744846f844374847c902b5e7fd59b4342a51ef99'
FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/foundation-source-oracle.json'
# Only imports and calls cross the foreign-runtime boundary. Cases and orchestration
# stay in Python; expected answers are never sent to the source implementation.
INVOKE = r'''
const req = await Bun.stdin.json();
const root = req.root;
const dict = await import(root + '/features/cli/dict-gate.ts');
const script = await import(root + '/features/cli/mixed-script-guard.ts');
const patterns = req.patterns.map(({pattern, flags, text}) => {
  const loaded = dict.loadDictionary(JSON.stringify({version:1,rules:[{id:'oracle',pattern,flags,replacement:'x'}]}), 'oracle.json');
  return {pattern,flags,text,kind:loaded.kind,matches:loaded.kind === 'ok' ? dict.checkDictionary(loaded.rules,[{label:'body',text,format:'plain'}]).map(h=>h.matched) : null};
});
console.log(JSON.stringify({bun:Bun.version,revision:Bun.revision,versions:process.versions,patterns,scripts:req.scripts.map(text=>({text,result:script.findMixedScriptTokens(text)}))}));
'''
IDENTITY = 'console.log(JSON.stringify({bun:Bun.version,revision:Bun.revision,versions:process.versions}))'


def run_bun(bun, script, request=None):
    result = subprocess.run([str(bun), '--eval', script], input=json.dumps(request),
                            text=True, capture_output=True, check=True, timeout=120)
    return json.loads(result.stdout)


def validate_runtime(identity):
    if (identity.get('bun'), identity.get('revision'), identity.get('versions', {}).get('unicode'),
        identity.get('versions', {}).get('icu')) != ('1.4.2', BUN_REVISION, '17.0', '78.3'):
        raise ValueError('Bun must be 1.4.2 at the pinned revision, Unicode 17.0 / ICU 78.3')


def validate_checkout(checkout):
    checkout = Path(checkout).resolve()
    try:
        head = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], stderr=subprocess.PIPE, text=True).strip()
        dirty = subprocess.check_output(['git', '-C', str(checkout), 'status', '--porcelain', '--untracked-files=all'], text=True)
    except subprocess.CalledProcessError as exc:
        raise ValueError('external checkout is required') from exc
    if head != SOURCE_REVISION or dirty:
        raise ValueError('external checkout must be clean at ' + SOURCE_REVISION)
    return checkout


def prepare(checkout, bun):
    checkout = validate_checkout(checkout)
    identity = run_bun(bun, IDENTITY)
    validate_runtime(identity)
    return checkout, identity


def capture(checkout, bun):
    checkout, _ = prepare(checkout, bun)
    cases = json.loads(FIXTURE.read_text())
    request = {'root': str(checkout),
               'patterns': [{key: row[key] for key in ('pattern', 'flags', 'text')} for row in cases['patterns']],
               'scripts': [row['text'] for row in cases['scripts']]}
    result = run_bun(bun, INVOKE, request)
    validate_runtime(result)
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkout', required=True, type=Path)
    p.add_argument('--bun', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    return p


def write_capture(args, content):
    # Refuse to overwrite the immutable expectation or any other existing artifact.
    with args.output.open('x', encoding='utf-8') as target:
        target.write(content)
    sources = subprocess.check_output(['git', '-C', str(args.checkout), 'ls-files', '-z']).split(b'\0')
    manifest = {'source_revision': SOURCE_REVISION, 'bun_revision': BUN_REVISION,
                'checkout': str(args.checkout.resolve()), 'bun': str(args.bun.resolve()),
                'unicode': '17.0', 'icu': '78.3',
                'inputs_sha256': hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
                'harnesses': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in Path(__file__).parent.glob('*.py')},
                'bun_sha256': hashlib.sha256(args.bun.read_bytes()).hexdigest(),
                'output_sha256': hashlib.sha256(content.encode()).hexdigest(),
                'sources': {name.decode(): hashlib.sha256((args.checkout / name.decode()).read_bytes()).hexdigest()
                            for name in sources if name and (args.checkout / name.decode()).is_file()}}
    with Path(str(args.output) + '.provenance.json').open('x') as target:
        json.dump(manifest, target, indent=2)
        target.write('\n')


if __name__ == '__main__':
    args = parser().parse_args()
    write_capture(args, json.dumps(capture(args.checkout, args.bun), ensure_ascii=False, indent=2) + '\n')
