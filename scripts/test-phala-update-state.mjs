import test from 'node:test';
import assert from 'node:assert/strict';
import {allowedUpdateState, CPU_FIX_SOURCE} from './phala-update-state.mjs';
const failed = {status: 'error', in_progress: false};
const stats = {status: 'stopped', in_progress: false, boot_error: 'failed to start containers'};
const report = {kind: 'reviewed_staging_cpu_quota_correction_only', source_sha256: CPU_FIX_SOURCE,
  removed_converter_cpu_quota: '8.0', converter_memory_limit_unchanged: '16g', serving_runtime_unchanged: true};
test('default rejects error state; explicit reviewed path accepts only diagnosed startup failure', () => {
  assert.equal(allowedUpdateState(failed, report, stats), false);
  assert.equal(allowedUpdateState(failed, report, stats, true), true);
  assert.equal(allowedUpdateState({status: 'running', in_progress: false}, {}, null), true);
});
test('active operation, different state, baseline, correction or unavailable diagnosis all fail closed', () => {
  for (const cvm of [{...failed, in_progress: true}, {...failed, status: 'stopped'}, {...failed, status: 'running'}])
    assert.equal(allowedUpdateState(cvm, report, stats, true), false);
  for (const changed of [{...report, source_sha256: 'different'}, {...report, kind: 'other'},
    {...report, converter_memory_limit_unchanged: '32g'}, {...report, serving_runtime_unchanged: false}])
    assert.equal(allowedUpdateState(failed, changed, stats, true), false);
  for (const changed of [null, {...stats, in_progress: true}, {...stats, boot_error: 'another failure'}])
    assert.equal(allowedUpdateState(failed, report, changed, true), false);
});
