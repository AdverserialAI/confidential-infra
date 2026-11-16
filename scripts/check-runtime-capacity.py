#!/usr/bin/env python3
"""Inside-CVM read-only capacity check; never submits an inference prompt."""
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request


def main():
    key = os.environ['BACKEND_API_KEY']
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request('http://127.0.0.1:30000/props',
                                         headers={'Authorization': 'Bearer ' + key})
            with urllib.request.urlopen(req, timeout=10) as response:
                props = json.load(response)
            break
        except Exception:
            time.sleep(10)
    else:
        raise SystemExit('runtime-capacity-check: backend metadata unavailable')
    report = {
        'model': props.get('model_alias'),
        'slots': props.get('total_slots'),
        'context_per_slot': props.get('default_generation_settings', {}).get('n_ctx'),
        'inference_prompts_submitted': 0,
    }
    root = Path('/data/cyberseek-q2-llama.cpp-no-peer-copy-c49edf0963a7')
    manifest = json.loads((root / 'BUILD_INFO.json').read_text())
    report['binary_sha256'] = hashlib.sha256((root / 'bin/llama-server').read_bytes()).hexdigest()
    report['runtime_revision'] = manifest.get('revision')
    report['cuda_no_peer_copy'] = manifest.get('cuda_no_peer_copy')
    report['verified'] = (
        report['model'] == 'adverserial/cyberseek' and report['slots'] == 64
        and report['context_per_slot'] == 1048576
        and report['runtime_revision'] == 'c49edf0963a76d5e78b2a7278c9bd5b71d26c84f'
        and report['cuda_no_peer_copy'] is True
        and report['binary_sha256'] == manifest.get('binary_sha256')
    )
    print(json.dumps({'runtime_capacity_check': report}), flush=True)
    if not report['verified']:
        raise SystemExit('runtime-capacity-check: deployed capacity/build mismatch')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Do not print HTTP response bodies, headers, API keys or private traces.
        raise SystemExit('runtime-capacity-check: verification error; private response withheld')
