#!/usr/bin/env python3
"""Bounded native vLLM-image/NCCL preflight, no model or prompt access.

Eight temporary ranks allocate one 4 MiB tensor each. No driver, GPU security,
runtime-service or rental settings are changed. Parent suppresses raw failures.
"""
import json
import os
import re
import subprocess
import sys

WORKER = r'''
import datetime,json,os,time
import torch
import torch.distributed as dist
rank=int(os.environ['RANK']);world=int(os.environ['WORLD_SIZE'])
torch.cuda.set_device(rank)
if torch.cuda.device_count()!=8 or torch.cuda.get_device_capability(rank)!=(9,0):
    raise RuntimeError('Unexpected GPU topology')
dist.init_process_group('nccl',timeout=datetime.timedelta(seconds=180))
x=torch.full((1048576,),float(rank),device=f'cuda:{rank}',dtype=torch.float32)
for _ in range(2):
    x.fill_(rank);dist.all_reduce(x)
torch.cuda.synchronize();start=time.monotonic()
for _ in range(10):
    x.fill_(rank);dist.all_reduce(x)
torch.cuda.synchronize();elapsed=time.monotonic()-start
verified=bool(torch.all(x==28).item())
report={'rank':rank,'verified':verified,'device_name':torch.cuda.get_device_name(rank),
        'compute_capability':list(torch.cuda.get_device_capability(rank)),
        'average_4MiB_allreduce_milliseconds':elapsed*100}
if rank==0:report.update({'torch_version':torch.__version__,'cuda_version':torch.version.cuda})
print(json.dumps(report),flush=True)
dist.destroy_process_group()
if not verified:raise SystemExit(2)
'''


def stderr_summary(stderr):
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    candidates = [line for line in reversed(lines) if re.search(
        r'(?:error|failed|exception|traceback|unsupported|invalid|unavailable|denied|refused|timeout|out of memory)',
        line, re.IGNORECASE)]
    summary = candidates[0] if candidates else (lines[-1] if lines else '')
    summary = re.sub(r'/(?:[^\s:;]+/)+[^\s:;]+', '<path>', summary)
    summary = re.sub(r'0x[0-9a-fA-F]+', '0x…', summary)
    return summary[:240]


def main():
    # Fixed transport settings avoid the unsupported direct GPU peer-copy
    # path while keeping all communication inside this container's TEE.
    env=dict(os.environ)
    env.update({'NCCL_P2P_DISABLE':'1','NCCL_IB_DISABLE':'1','NCCL_NET_GDR_LEVEL':'0',
                'NCCL_CUMEM_HOST_ENABLE':'0','NCCL_SOCKET_IFNAME':'lo',
                'MASTER_ADDR':'127.0.0.1','MASTER_PORT':'29561','WORLD_SIZE':'8',
                'OMP_NUM_THREADS':'1','NCCL_DEBUG':'WARN'})
    flags=['--tokenizer-mode','--reasoning-parser','--tool-call-parser','--engram-config',
           '--max-num-seqs','--max-model-len','--enable-chunked-prefill',
           '--max-num-batched-tokens','--disable-custom-all-reduce','--kv-cache-dtype']
    cli=subprocess.run(['vllm','serve','--help=all'],capture_output=True,text=True,timeout=120)
    missing=[flag for flag in flags if flag not in cli.stdout]
    print(json.dumps({'event':'vllm_cli_preflight','passed':cli.returncode==0 and not missing,
                      'missing_required_flags':missing}),flush=True)
    if cli.returncode or missing:return 1
    children=[]
    try:
        for rank in range(8):
            children.append(subprocess.Popen([sys.executable,'-B','-c',WORKER],
                env={**env,'RANK':str(rank),'LOCAL_RANK':str(rank)},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
        reports=[];failures=[]
        for rank,child in enumerate(children):
            stdout,stderr=child.communicate(timeout=240)
            if child.returncode:
                failure = {'rank':rank,'exit_code':child.returncode,
                    'unsupported_operation_observed':'operation not supported' in stderr.lower(),
                    'out_of_memory_observed':'out of memory' in stderr.lower(),
                    'raw_stderr_bytes_withheld':len(stderr.encode())}
                summary=stderr_summary(stderr)
                if summary:failure['error_summary']=summary
                failures.append(failure)
            else:
                result=json.loads(stdout.strip().splitlines()[-1])
                allowed=['rank','verified','device_name','compute_capability',
                         'average_4MiB_allreduce_milliseconds','torch_version','cuda_version']
                reports.append({k:result[k] for k in allowed if k in result})
        passed=not failures and len(reports)==8 and all(r.get('verified') is True for r in reports)
        print(json.dumps({'event':'confidential_gpu_comms_preflight','passed':passed,
                          'world_size':8,'reports':reports,'failures':failures,
                          'model_or_prompt_access':False,'security_or_driver_changes':False}),flush=True)
        return 0 if passed else 1
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()  # Own temporary probe rank only, never the model/CVM.
                child.wait(timeout=10)


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'event':'gpu_comms_preflight_failed','error_class':type(error).__name__}),flush=True)
        raise SystemExit(1)
