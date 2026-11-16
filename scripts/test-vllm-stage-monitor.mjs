import test from 'node:test';
import assert from 'node:assert/strict';
import {summarize, expectedSha} from './monitor-vllm-stage.mjs';

test('monitor retains bounded native progress and drops private trace/body fields', () => {
  const raw = [
    '2026-11-16T09:30:00Z {"event":"native_shard_completed","shard":3,"bytes":123,"prompt":"PRIVATE","api_key":"SECRET"}',
    '{"event":"arbitrary-private-event","body":"PRIVATE"}',
    'Authorization: Bearer SECRET',
    'CUDA error: operation not supported',
  ].join('\n');
  const report = summarize(raw);
  assert.deepEqual(report.events, [{event: 'native_shard_completed', shard: 3, bytes: 123}]);
  assert.equal(report.unsupported_operation_errors, 1);
  assert.equal(report.last_timestamp, '2026-11-16T09:30:00Z');
  assert.doesNotMatch(JSON.stringify(report), /PRIVATE|SECRET|Bearer/);
});

test('monitor copies only rank/result numbers and a complete measured digest', () => {
  const event = {event: 'confidential_gpu_comms_preflight', passed: true, world_size: 8,
    reports: [{rank: 0, verified: true, average_4MiB_allreduce_milliseconds: 2, device_name: 'PRIVATE'}],
    failures: [{rank: 1, exit_code: 'SECRET', unsupported_operation_observed: true, raw_stderr: 'PRIVATE'}]};
  const report = summarize(JSON.stringify(event) + '\nmodel artifact measured: files=9 bytes=123 digest=sha256:' + 'a'.repeat(64));
  assert.deepEqual(report.events[0].verified_ranks, [0]);
  assert.deepEqual(report.events[0].failed_ranks, [{rank: 1, unsupported_operation_observed: true, out_of_memory_observed: false}]);
  assert.equal(report.measurement.files, 9);
  assert.doesNotMatch(JSON.stringify(report), /PRIVATE|SECRET|raw_stderr/);
});

test('monitor accepts only an explicit reviewed corrected SHA override', () => {
  assert.equal(expectedSha([]), '9c8455bd7ee1134f87277c72aaa92c6d493e44c2adf4ac4abfe9c76813f1ae0a');
  assert.equal(expectedSha(['--expected-sha', '3'.repeat(64)]), '3'.repeat(64));
  for (const args of [['--expected-sha'], ['--expected-sha', 'not-a-sha'], ['--expected-sha', 'A'.repeat(64)]]) {
    assert.throws(() => expectedSha(args));
  }
});
