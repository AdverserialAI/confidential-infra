#!/usr/bin/env node
// One ordinary update of an existing CVM. No stop/delete/restart/rental API is used.
// Default mode is read-only. The caller must already have explicit user approval
// before invoking --apply; this program does not manufacture that authorization.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { allowedUpdateState, sameResourceAllocation } from './phala-update-state.mjs';

const args = process.argv.slice(2);
const apply = args.includes('--apply');
const reviewedStartupError = args.includes('--reviewed-startup-error');
function arg(name) {
  const index = args.indexOf(name);
  if (index < 0 || !args[index + 1] || args[index + 1].startsWith('--')) {
    throw new Error('Missing required argument');
  }
  return args[index + 1];
}
function hash(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}
function fail(message) {
  throw new Error(message);
}

try {
  const sdkPath = arg('--sdk');
  const baselinePath = arg('--baseline');
  const composePath = arg('--compose');
  const reportPath = arg('--report');
  const evidencePath = arg('--evidence');
  const baseline = JSON.parse(fs.readFileSync(baselinePath, 'utf8'));
  const report = JSON.parse(fs.readFileSync(reportPath, 'utf8'));
  const compose = fs.readFileSync(composePath, 'utf8');
  if (hash(compose) !== report.prepared_sha256) fail('Prepared file hash changed');
  if (hash(baseline.compose_file.docker_compose_file) !== report.source_sha256) {
    fail('Baseline file hash changed');
  }
  if (report.slots !== 64 || report.context_tokens_per_slot !== 1048576 ||
      report.total_context_tokens !== 67108864 || report.max_output_tokens !== 65536 ||
      report.kv_unified !== false || report.unrelated_semantic_changes !== false) {
    fail('Unexpected reviewed capacity');
  }
  if (report.model !== 'adverserial/cyberseek') fail('Unexpected reviewed model');
  if (baseline.project_type !== 'gpu-rental/on-demand' || baseline.kms_type !== 'phala') {
    fail('Unexpected rental or KMS type');
  }
  const { createClient, safeGetCvmInfo, safeGetCvmStats, safePatchCvm } = await import(pathToFileURL(sdkPath));
  const directory = process.env.PHALA_CLOUD_DIR || path.join(os.homedir(), '.phala-cloud');
  const credentials = JSON.parse(fs.readFileSync(path.join(directory, 'credentials.json'), 'utf8'));
  const profile = credentials.profiles[credentials.current_profile];
  const baseURL = process.env.PHALA_CLOUD_API_PREFIX || profile.api_prefix || 'https://cloud-api.phala.com/api/v1';
  if (baseURL !== 'https://cloud-api.phala.com/api/v1') fail('Unexpected cloud API authority');
  const client = createClient({
    apiKey: process.env.PHALA_CLOUD_API_KEY || profile.token,
    baseURL, version: '2026-06-23', timeout: 60000,
  });
  const current = await safeGetCvmInfo(client, { id: baseline.id });
  if (!current.success) fail('Could not read current CVM; no update submitted');
  const cvm = current.data;
  if (cvm.id !== baseline.id || cvm.vm_uuid !== baseline.vm_uuid || cvm.app_id !== baseline.app_id) {
    fail('CVM identity changed; no update submitted');
  }
  // Narrow opt-in for the independently diagnosed CPU-quota startup failure.
  // This is still an ordinary PATCH, never a stop/start/restart, and --apply
  // still requires fresh explicit owner approval of the exact correction.
  const stats = reviewedStartupError ? await safeGetCvmStats(client, {id: cvm.id}) : null;
  if (!allowedUpdateState(cvm, report, stats?.success ? stats.data : null, reviewedStartupError)) {
    fail('Current CVM state does not match reviewed update preconditions; no update submitted');
  }
  if (cvm.public_logs !== true) fail('Diagnostic log activation has not completed');
  if (cvm.project_type !== baseline.project_type || cvm.kms_type !== baseline.kms_type) {
    fail('Rental or KMS configuration changed');
  }
  if (!sameResourceAllocation(cvm.resource, baseline.resource)) {
    fail('Reported hardware allocation changed or unavailable; no update submitted');
  }
  if (hash(cvm.compose_file.docker_compose_file) !== report.source_sha256) {
    fail('Live Docker Compose changed; review again before updating');
  }
  if (JSON.stringify(cvm.compose_file) !== JSON.stringify(baseline.compose_file)) {
    // Ignore JSON property order, but require every field/value to match.
    const fields = [...new Set([...Object.keys(cvm.compose_file), ...Object.keys(baseline.compose_file)])];
    for (const field of fields) {
      if (JSON.stringify(cvm.compose_file[field]) !== JSON.stringify(baseline.compose_file[field])) {
        fail('Live app configuration changed; review again before updating');
      }
    }
  }
  const evidence = {
    observed_at_utc: new Date().toISOString(),
    cvm_id: cvm.id, vm_uuid: cvm.vm_uuid,
    requested_context_per_slot: 1048576, requested_slots: 64,
    max_output_tokens: 65536, allow_force_stop: false,
    prepared_sha256: report.prepared_sha256,
    reviewed_startup_error_correction: reviewedStartupError,
    provider_status: cvm.status,
    reported_hardware_allocation_preserved: true,
    mode: apply ? 'apply' : 'read-only-preflight',
    update_submitted: false,
  };
  // Prevent an accidental repeated submission after an ambiguous network result.
  const fd = fs.openSync(evidencePath, 'wx', 0o600);
  fs.writeFileSync(fd, JSON.stringify(evidence, null, 2) + '\n');
  fs.closeSync(fd);
  if (apply) {
    evidence.update_attempted = true;
    fs.writeFileSync(evidencePath, JSON.stringify(evidence, null, 2) + '\n');
    const result = await safePatchCvm(client, {
      id: cvm.id,
      docker_compose_file: compose,
      public_logs: true,
      allow_force_stop: false,
    });
    if (!result.success) {
      evidence.outcome = 'not-confirmed-do-not-retry-without-checking-provider-state';
      evidence.http_status = result.error?.status;
      fs.writeFileSync(evidencePath, JSON.stringify(evidence, null, 2) + '\n');
      fail('Update acceptance unconfirmed; inspect provider state before any retry');
    }
    evidence.update_submitted = true;
    evidence.requires_on_chain_hash = result.data.requiresOnChainHash;
    evidence.correlation_id = result.data.correlationId;
    fs.writeFileSync(evidencePath, JSON.stringify(evidence, null, 2) + '\n');
  }
  console.log(JSON.stringify(evidence, null, 2));
} catch (error) {
  // SDK errors can contain private response payloads; never dump them.
  const safeMessages = new Set([
    'Missing required argument', 'Prepared file hash changed', 'Baseline file hash changed',
    'Unexpected reviewed capacity', 'Unexpected reviewed model', 'Unexpected rental or KMS type',
    'Unexpected cloud API authority', 'Could not read current CVM; no update submitted',
    'CVM identity changed; no update submitted',
    'Current CVM state does not match reviewed update preconditions; no update submitted',
    'Diagnostic log activation has not completed', 'Rental or KMS configuration changed',
    'Reported hardware allocation changed or unavailable; no update submitted',
    'Live Docker Compose changed; review again before updating',
    'Live app configuration changed; review again before updating',
    'Update acceptance unconfirmed; inspect provider state before any retry',
  ]);
  console.error(safeMessages.has(error?.message) ? error.message : 'Guard failed; private diagnostic data withheld');
  process.exitCode = 1;
}
