#!/usr/bin/env python3
"""Serving boundary tests use synthetic credentials and no cloud service."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import yaml


def load(name, alias):
    spec = importlib.util.spec_from_file_location(alias, Path(__file__).with_name(name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load('prepare-vllm-serving.py', 'serving')
stage = load('prepare-vllm-stage.py', 'stage')


def fixture():
    key = 'synthetic-only-loopback-key'
    volumes = [
        {'type': 'volume', 'source': 'cyberglm-data', 'target': '/model', 'read_only': True, 'volume': {}},
        {'type': 'volume', 'source': 'model-evidence-state', 'target': '/model-evidence', 'volume': {}},
    ]
    command = ['/data/runtime/bin/llama-server', '--ctx-size', '67108864', '--parallel', '64',
               '--n-predict', '65536', '--ubatch-size', '32', '--alias', 'adverserial/cyberseek',
               '--host', '127.0.0.1', '--api-key', key, '--no-kv-unified']
    tree = {'services': {
        'attest-proxy': {'image': 'synthetic-proxy@sha256:' + 'a' * 64,
                         'environment': {'COMPOSE_DIGEST': 'sha256:' + 'b' * 64,
                                         'CONFIDENTIAL_MODE': '1', 'AUTH_REQUIRED': '0', 'EHBP_REQUIRED': '1',
                                         'MODEL_ID': 'adverserial/cyberseek', 'UPSTREAM_BEARER_TOKEN': key,
                                         'RECEIPT_SIGNING_SEED': 'synthetic-only-receipt-seed'},
                         'depends_on': {'model-measurer': {'condition': 'service_completed_successfully'}}},
        'sglang': {'command': command, 'volumes': volumes[:1],
                   'deploy': {'resources': {'reservations': {'devices': [{'driver': 'nvidia', 'count': 8, 'capabilities': ['gpu']}]}}}},
        'model-measurer': {'image': 'synthetic-measurer@sha256:' + 'c' * 64,
                           'environment': {'MODEL_ROOT': '/model/cyberseek-q2', 'MODEL_ID': 'adverserial/cyberseek',
                                           'MODEL_MANIFEST_FILE': '/model-evidence/manifest.json'},
                           'volumes': volumes, 'depends_on': {'model-perms': {'condition': 'service_completed_successfully'}},
                           'network_mode': 'none', 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true']},
        'model-download': {'command': ['synthetic-only-recovery']},
        'model-perms': {'depends_on': {'model-download': {'condition': 'service_completed_successfully'}}},
        'runtime-capacity-check': {'depends_on': {'sglang': {'condition': 'service_started'}}},
        'unrelated': {'environment': {'KEEP': 'synthetic-unrelated-value'}, 'network_mode': 'none'},
    }, 'volumes': {'cyberglm-data': {}, 'model-evidence-state': {}}}
    return stage.prepare(yaml.safe_dump(tree, sort_keys=False), m.CONVERTER,
                         'vllm/vllm-openai@sha256:91ee972ab751fb7180bc71c175865e36b091686600dca783d52b2511399810b3',
                         "print('synthetic-comms')\n")[0]


def prepare_fixture(source=None):
    source = fixture() if source is None else source
    with patch.object(m, 'STAGE_SHA256', hashlib.sha256(source.encode()).hexdigest()):
        return m.prepare(source)


class ServingTests(unittest.TestCase):
    def test_preserves_keys_proxy_controls_and_recovery_without_public_backend(self):
        source = fixture()
        before = yaml.safe_load(source)
        rendered, report = prepare_fixture(source)
        after = yaml.safe_load(rendered)
        self.assertEqual(after['volumes'], before['volumes'])
        self.assertEqual(after['services']['unrelated'], before['services']['unrelated'])
        a = copy.deepcopy(after['services']['attest-proxy'])
        b = copy.deepcopy(before['services']['attest-proxy'])
        for x in [a, b]:
            x.pop('depends_on')
            for key in ['COMPOSE_DIGEST', 'RUNTIME_DIGEST', 'MODEL_MANIFEST_FILE']:
                x['environment'].pop(key, None)
        self.assertEqual(a, b)
        engine = after['services']['sglang']
        self.assertNotIn('ports', engine)
        self.assertNotIn('privileged', engine)
        self.assertEqual(engine['user'], '65532:65532')
        self.assertEqual(engine['cap_drop'], ['ALL'])
        self.assertEqual(engine['network_mode'], 'service:attest-proxy')
        self.assertEqual(engine['command'][-2:], ['--api-key', 'synthetic-only-loopback-key'])
        self.assertTrue(all(v['read_only'] for v in engine['volumes']))
        self.assertEqual(engine['environment']['VLLM_SERVER_DEV_MODE'], '0')
        self.assertIn('--no-enable-prefix-caching', engine['command'])
        self.assertNotIn('HF_TOKEN', engine['environment'])
        self.assertNotIn('synthetic-only-loopback-key', str(report))
        self.assertTrue(report['approval_required'])
        self.assertFalse(report['gpu_startup_speed_quality_and_full_capacity_verified'])
        for name in ['model-download', 'model-perms']:
            wanted = dict(before['services'][name], profiles=['recovery-only'])
            self.assertEqual(after['services'][name], wanted)

    def test_startup_graph_has_no_cycle_and_gates_proxy_on_native_artifact_and_gpu(self):
        rendered, _ = prepare_fixture()
        services = yaml.safe_load(rendered)['services']
        seen = set()
        def visit(name, parents):
            self.assertNotIn(name, parents)
            if name in seen:
                return
            for dep in services[name].get('depends_on', {}):
                visit(dep, parents | {name})
            seen.add(name)
        for name in services:
            visit(name, set())
        self.assertNotIn('depends_on', services['model-measurer'])
        checker = services['q2-native-reconstruction']
        self.assertEqual(checker['network_mode'], 'none')
        self.assertNotIn('deploy', checker)
        self.assertTrue(all(v['read_only'] for v in checker['volumes']))
        self.assertIn('vllm-communication-preflight', services['attest-proxy']['depends_on'])
        self.assertIn('q2-native-reconstruction', services['attest-proxy']['depends_on'])
        self.assertIn('model-measurer', checker['depends_on'])

    def test_unknown_baseline_or_altered_confidential_controls_fail(self):
        with self.assertRaises(ValueError):
            m.prepare(fixture())
        for change in ['EHBP_REQUIRED', 'UPSTREAM_BEARER_TOKEN', 'MODEL_DIGEST']:
            tree = yaml.safe_load(fixture())
            tree['services']['attest-proxy']['environment'][change] = 'unexpected'
            with self.subTest(change=change), self.assertRaises(ValueError):
                prepare_fixture(yaml.safe_dump(tree, sort_keys=False))

    def test_profile_change_rejected_and_readiness_never_claims_full_capacity(self):
        with patch.object(m, 'PROFILE_SHA256', '0' * 64), self.assertRaises(ValueError):
            prepare_fixture()
        rendered, report = prepare_fixture()
        services = yaml.safe_load(rendered)['services']
        observation = services['runtime-capacity-check']['command'][0]
        self.assertIn("'full_64_by_1M_capacity_verified':False", observation)
        self.assertNotIn('/server_info', observation)
        self.assertNotIn('/chat/completions', observation)
        self.assertEqual(report['configured_max_num_seqs'], 64)
        self.assertEqual(report['configured_max_model_len'], 1048576)
        self.assertEqual(report['configured_output_cap'], 65536)


if __name__ == '__main__':
    unittest.main()
