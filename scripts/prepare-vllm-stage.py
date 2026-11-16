#!/usr/bin/env python3
"""Add isolated native conversion and GPU preflight; retain serving runtime.

Local private file preparation only. Guarded normal CVM update is separate.
Existing service/config bytes are preserved except the deployment digest.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import yaml

spec=importlib.util.spec_from_file_location('context_update',Path(__file__).with_name('prepare-llama-context-update.py'))
context=importlib.util.module_from_spec(spec);spec.loader.exec_module(context)


def prepare(source, converter_image, runtime_image, comms_script):
    if not re.fullmatch(r'ghcr\.io/adverserialai/q2-native-converter@sha256:[0-9a-f]{64}',converter_image):
        raise ValueError('Converter image must be immutable and from the reviewed namespace')
    if runtime_image!='vllm/vllm-openai@sha256:91ee972ab751fb7180bc71c175865e36b091686600dca783d52b2511399810b3':
        raise ValueError('Unexpected pinned native runtime image')
    before=yaml.safe_load(source)
    cmd=before['services']['sglang']['command']
    for flag,value in {'--ctx-size':'67108864','--parallel':'64','--n-predict':'65536','--ubatch-size':'32','--alias':'adverserial/cyberseek','--host':'127.0.0.1'}.items():
        if cmd.count(flag)!=1 or cmd[cmd.index(flag)+1]!=value:raise ValueError('Serving baseline differs from reviewed configuration')
    if '--no-kv-unified' not in cmd or not cmd[0].endswith('/llama-server'):raise ValueError('Unexpected serving runtime/context mode')
    measurer=before['services']['model-measurer']
    evidence_file=measurer['environment']['MODEL_MANIFEST_FILE']
    if not re.fullmatch(r'/model-evidence/[A-Za-z0-9._/-]+',evidence_file) or '..' in evidence_file:raise ValueError('Unsafe source manifest path')
    if measurer['environment']['MODEL_ROOT']!='/model/cyberseek-q2':raise ValueError('Unexpected source artifact root')
    conversion={'image':converter_image,'restart':'no','init':True,'read_only':True,
        'user':'0:0','cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],
        'cpus':'8.0','mem_limit':'16g','pids_limit':256,'tmpfs':['/tmp:size=64m,mode=1777'],
        'environment':{'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'},
        'command':['--source','/source/cyberseek-q2','--source-manifest',evidence_file.replace('/model-evidence/','/source-evidence/'),
                   '--plan','/app/q2-native-compact-plan.json','--output','/native'],
        'depends_on':{'runtime-capacity-check':{'condition':'service_completed_successfully','required':True}},
        'volumes':[{'type':'volume','source':'cyberglm-data','target':'/source','read_only':True,'volume':{}},
                   {'type':'volume','source':'model-evidence-state','target':'/source-evidence','read_only':True,'volume':{}},
                   {'type':'volume','source':'cyberseek-vllm-native-data','target':'/native','volume':{}}]}
    preflight={'image':runtime_image,'restart':'no','init':True,'read_only':True,
        'cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],'network_mode':'none',
        'mem_limit':'24g','pids_limit':256,'shm_size':'1g','tmpfs':['/tmp:size=256m,mode=1777'],
        'ulimits':{'memlock':{'soft':-1,'hard':-1}},
        'entrypoint':['python3','-B','-c'],'command':[comms_script.replace('$','$$')],
        'environment':{'PYTHONDONTWRITEBYTECODE':'1','VLLM_CACHE_ROOT':'/tmp/vllm',
                       'XDG_CACHE_HOME':'/tmp/cache','HF_HOME':'/tmp/hf',
                       'TORCHINDUCTOR_CACHE_DIR':'/tmp/torchinductor','TRITON_CACHE_DIR':'/tmp/triton'},
        'deploy':{'resources':{'reservations':{'devices':[{'driver':'nvidia','count':8,'capabilities':['gpu']}]}}},
        'depends_on':{'runtime-capacity-check':{'condition':'service_completed_successfully','required':True}}}
    additions={'q2-native-reconstruction':conversion,'vllm-communication-preflight':preflight}
    if any(n in before['services'] for n in additions) or 'cyberseek-vllm-native-data' in before.get('volumes',{}):
        raise ValueError('Staging components already present; inspect rather than stack an update')
    tree=yaml.compose(source)
    def child(mapping,name):return next(v for k,v in mapping.value if k.value==name)
    services_node,volumes_node=child(tree,'services'),child(tree,'volumes')
    inserts=[(services_node.end_mark.index,yaml.safe_dump(additions,sort_keys=False,indent=2)),
             (volumes_node.end_mark.index,yaml.safe_dump({'cyberseek-vllm-native-data':{}},sort_keys=False))]
    rendered=source
    for position,block in sorted(inserts,reverse=True):
        # Both root mappings' children have two-space indentation.
        block=''.join('  '+line if line.strip() else line for line in block.splitlines(keepends=True))
        line_start=rendered.rfind('\n',0,position)+1
        if rendered[line_start:position].strip():raise ValueError('Unsafe YAML insertion boundary')
        rendered=rendered[:line_start]+block+rendered[line_start:]
    digest=context.configuration_digest(rendered,before)
    rendered,count=re.subn(r'^(\s*COMPOSE_DIGEST:\s*).*?$',lambda m:m[1]+digest,rendered,flags=re.M)
    if count!=1:raise ValueError('Unexpected deployment digest count')
    permitted=copy.deepcopy(before)
    permitted['services'].update(additions)
    permitted['volumes']['cyberseek-vllm-native-data']={}
    permitted['services']['attest-proxy']['environment']['COMPOSE_DIGEST']=digest
    if yaml.safe_load(rendered)!=permitted:raise ValueError('Unexpected unrelated semantic change')
    return rendered,{'kind':'isolated_native_staging_only_existing_serving_configuration_preserved',
        'model':'adverserial/cyberseek','slots':64,'context_tokens_per_slot':1048576,'total_context_tokens':67108864,
        'max_output_tokens':65536,'kv_unified':False,'unrelated_semantic_changes':False,
        'converter_image':converter_image,'native_runtime_preflight_image':runtime_image,
        'serving_runtime_unchanged':True,'active_model_inputs_read_only':True,'new_native_volume_only':True,
        'deployment_configuration_digest':digest,
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'prepared_sha256':hashlib.sha256(rendered.encode()).hexdigest(),'provider_mutations':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--converter-image',required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args()
    rendered,report=prepare(a.source.read_text(),a.converter_image,
        'vllm/vllm-openai@sha256:91ee972ab751fb7180bc71c175865e36b091686600dca783d52b2511399810b3',
        Path(__file__).with_name('check-confidential-gpu-comms.py').read_text())
    context.private_write(a.output,rendered);context.private_write(a.report,json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'stage_preparation_failed':True,'error_class':type(error).__name__}))
        raise SystemExit(1)
