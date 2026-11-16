#!/usr/bin/env python3
"""Create an exclusive, allowlisted converter-image context without secrets."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--compact-plan',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    plan=json.loads(a.compact_plan.read_text())
    if plan.get('compact_plan_version') != 1 or plan.get('patch',{}).get('variant') != 'Q2_2' or len(plan.get('projections',[])) != 54:
        raise ValueError('Unexpected compact reconstruction plan')
    a.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[1]
    files={'Dockerfile':root/'docker/q2-native-converter/Dockerfile',
           'q2-native-compact-plan.json':a.compact_plan}
    for name in ['reconstruct-q2-native.py','q2-native-quant.py','plan-q2-native-overlay.py',
                 'test-q2-native-quant.py','test-q2-native-reconstruction.py']:
        files[name]=root/'scripts'/name
    report={}
    for name,source in files.items():
        if source.is_symlink() or not source.is_file():raise ValueError('Nonregular context input')
        target=a.output/name
        shutil.copyfile(source,target)
        report[name]={'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
    (a.output/'context-manifest.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'context_files':report,'model_weights_or_credentials_included':False},indent=2))


if __name__=='__main__':main()
