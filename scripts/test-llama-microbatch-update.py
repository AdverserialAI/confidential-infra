#!/usr/bin/env python3
"""Verify workspace correction preserves context, auth and unrelated services."""
import importlib.util
from pathlib import Path
import unittest
import yaml

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

module = load('microbatch', 'prepare-llama-microbatch-update.py')
original_tests = load('context_tests', 'test-llama-context-update.py')

def fixture():
    x = yaml.safe_load(original_tests.FIXTURE)
    s = x['services']['sglang']
    s['command'][0] = '/data/cyberseek-q2-llama.cpp-no-peer-copy-c49edf0963a7/bin/llama-server'
    s['command'][s['command'].index('--ctx-size') + 1] = '67108864'
    s['command'] += ['--batch-size', '4096', '--ubatch-size', '512',
                     '--n-gpu-layers', '999', '--no-kv-unified', '--n-predict', '65536']
    x['services']['runtime-capacity-check'] = {
        'command': ['python3', '-B', '-c', 'deadline = time.monotonic() + 900\n'],
        'read_only': True,
    }
    return yaml.safe_dump(x, sort_keys=False)

class MicrobatchTests(unittest.TestCase):
    def test_only_workspace_wait_and_digest_change(self):
        source = fixture()
        rendered, report = module.prepare(source)
        a, b = yaml.safe_load(source), yaml.safe_load(rendered)
        command = b['services']['sglang']['command']
        self.assertEqual(command[command.index('--ubatch-size') + 1], '32')
        for flag, val in [('--ctx-size','67108864'),('--parallel','64'),('--n-predict','65536'),('--n-gpu-layers','999')]:
            self.assertEqual(command[command.index(flag) + 1], val)
        self.assertIn('--no-kv-unified', command)
        a['services']['sglang']['command'] = command
        a['services']['runtime-capacity-check']['command'][3] = b['services']['runtime-capacity-check']['command'][3]
        a['services']['attest-proxy']['environment']['COMPOSE_DIGEST'] = b['services']['attest-proxy']['environment']['COMPOSE_DIGEST']
        self.assertEqual(a, b)
        self.assertEqual(report['deployment_configuration_digest'], module.context.configuration_digest(rendered, b))
        self.assertNotIn('fixture-only', str(report))

    def test_refuses_unreviewed_runtime_or_session_changes(self):
        for flag, value in [('--parallel','32'),('--ctx-size','1048576'),('--host','0.0.0.0'),('--ubatch-size','256')]:
            x = yaml.safe_load(fixture())
            cmd = x['services']['sglang']['command']
            cmd[cmd.index(flag) + 1] = value
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                module.prepare(yaml.safe_dump(x, sort_keys=False))

    def test_refuses_ambiguous_observer(self):
        x = yaml.safe_load(fixture())
        x['services']['runtime-capacity-check']['command'][3] = 'unknown observer'
        with self.assertRaises(ValueError):
            module.prepare(yaml.safe_dump(x, sort_keys=False))

if __name__ == '__main__':
    unittest.main()
