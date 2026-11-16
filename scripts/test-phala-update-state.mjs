import test from 'node:test';
import assert from 'node:assert/strict';
import {allowedUpdateState, CPU_FIX_SOURCE, sameResourceAllocation} from './phala-update-state.mjs';
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
test('resource preservation rejects a resize, GPU or disk change and unavailable metadata', () => {
  const baseline = {instance_type: 'h200.8x.large', vcpu: 128, memory_in_gb: 1536, disk_in_gb: 2048, gpus: 8};
  assert.equal(sameResourceAllocation({...baseline}, baseline), true);
  for (const [key, value] of [['vcpu', 1], ['memory_in_gb', 16], ['disk_in_gb', 1024],
    ['gpus', 1], ['instance_type', 'different']]) {
    assert.equal(sameResourceAllocation({...baseline, [key]: value}, baseline), false);
  }
  assert.equal(sameResourceAllocation(null, baseline), false);
  assert.equal(sameResourceAllocation(baseline, {}), false);
  assert.equal(sameResourceAllocation({...baseline, vcpu: undefined}, baseline), false);
});
