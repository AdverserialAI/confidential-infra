#!/usr/bin/env python3
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import yaml

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

fix = load('cpu_fix', 'prepare-vllm-stage-cpu-fix.py')
stage = load('stage_fixture', 'test-vllm-stage-preparation.py')

class CPUFixTests(unittest.TestCase):
    def source(self):
        prepared, _ = stage.m.prepare(stage.fixture(), stage.IMAGE, stage.RUNTIME, 'probe')
        data = yaml.safe_load(prepared)
        data['services']['q2-native-reconstruction']['cpus'] = '8.0'
        return yaml.safe_dump(data, sort_keys=False)

    def test_removes_only_rejected_quota_and_updates_digest(self):
        source = self.source()
        with patch.object(fix, 'SOURCE_SHA256', fix.hashlib.sha256(source.encode()).hexdigest()):
            rendered, report = fix.prepare(source)
        before, after = yaml.safe_load(source), yaml.safe_load(rendered)
        permitted = copy.deepcopy(before)
        del permitted['services']['q2-native-reconstruction']['cpus']
        permitted['services']['attest-proxy']['environment']['COMPOSE_DIGEST'] = report['deployment_configuration_digest']
        self.assertEqual(after, permitted)
        self.assertNotIn('cpus', after['services']['q2-native-reconstruction'])
        self.assertEqual(after['services']['sglang'], before['services']['sglang'])
        self.assertEqual(after['services']['q2-native-reconstruction']['mem_limit'], '16g')
        self.assertNotIn('synthetic-', str(report))
        self.assertEqual(fix.context.configuration_digest(rendered, after), report['deployment_configuration_digest'])

    def test_unreviewed_baseline_and_other_resource_change_rejected(self):
        source = self.source()
        with self.assertRaises(ValueError): fix.prepare(source)
        changed = source.replace('mem_limit: 16g', 'mem_limit: 32g')
        with patch.object(fix, 'SOURCE_SHA256', fix.hashlib.sha256(changed.encode()).hexdigest()):
            with self.assertRaises(ValueError): fix.prepare(changed)

if __name__ == '__main__': unittest.main()
