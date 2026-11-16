#!/usr/bin/env python3
"""Verify staging cannot rewrite the active runtime, secrets or source volume."""
import copy
import importlib.util
from pathlib import Path
import unittest
import yaml

spec=importlib.util.spec_from_file_location('stage',Path(__file__).with_name('prepare-vllm-stage.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
IMAGE='ghcr.io/adverserialai/q2-native-converter@sha256:'+'a'*64
RUNTIME='vllm/vllm-openai@sha256:91ee972ab751fb7180bc71c175865e36b091686600dca783d52b2511399810b3'


def fixture():
    command=['/data/runtime/bin/llama-server','--ctx-size','67108864','--parallel','64',
             '--n-predict','65536','--ubatch-size','32','--alias','adverserial/cyberseek',
             '--host','127.0.0.1','--api-key','synthetic-loopback-token','--no-kv-unified']
    d={'services':{'attest-proxy':{'environment':{'COMPOSE_DIGEST':'sha256:'+'b'*64,'RECEIPT_SIGNING_SEED':'synthetic-receipt-seed'}},
        'sglang':{'command':command,'volumes':['cyberglm-data:/data:ro']},
        'model-measurer':{'environment':{'MODEL_ROOT':'/model/cyberseek-q2','MODEL_MANIFEST_FILE':'/model-evidence/manifest.json'}},
        'runtime-capacity-check':{'command':['metadata-only']}},
       'volumes':{'cyberglm-data':{},'model-evidence-state':{}}}
    return yaml.safe_dump(d,sort_keys=False)


class StageTests(unittest.TestCase):
    def test_only_isolated_services_volume_and_digest_are_added(self):
        source=fixture();before=yaml.safe_load(source)
        rendered,report=m.prepare(source,IMAGE,RUNTIME,"print('probe')\n")
        after=yaml.safe_load(rendered)
        self.assertEqual(after['services']['sglang'],before['services']['sglang'])
        self.assertEqual(after['services']['model-measurer'],before['services']['model-measurer'])
        self.assertEqual(after['services']['attest-proxy']['environment']['RECEIPT_SIGNING_SEED'],'synthetic-receipt-seed')
        conversion=after['services']['q2-native-reconstruction']
        self.assertNotIn('deploy',conversion)
        self.assertNotIn('ports',conversion)
        self.assertEqual(conversion['cap_drop'],['ALL'])
        self.assertTrue(conversion['volumes'][0]['read_only'])
        self.assertTrue(conversion['volumes'][1]['read_only'])
        self.assertEqual(conversion['depends_on']['runtime-capacity-check']['condition'],'service_completed_successfully')
        probe=after['services']['vllm-communication-preflight']
        self.assertEqual(probe['network_mode'],'none');self.assertNotIn('HOME',probe['environment'])
        self.assertTrue(report['serving_runtime_unchanged'])
        self.assertEqual(report['source_sha256'],m.hashlib.sha256(source.encode()).hexdigest())

    def test_invalid_images_changed_capacity_and_duplicate_stage_fail(self):
        for change in ['image','runtime','capacity','duplicate']:
            source=fixture();image=IMAGE;runtime=RUNTIME
            if change=='image':image='python:latest'
            if change=='runtime':runtime='vllm/vllm-openai:latest'
            if change=='capacity':source=source.replace("'67108864'","'1048576'")
            if change=='duplicate':source=m.prepare(source,image,runtime,'probe')[0]
            with self.subTest(change=change),self.assertRaises(ValueError):m.prepare(source,image,runtime,'probe')


if __name__=='__main__':unittest.main()
