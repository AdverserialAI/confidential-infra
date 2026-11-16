#!/usr/bin/env node
// Read-only observation of the approved staging operation. Never mutates a CVM.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn} from 'node:child_process';
import {pathToFileURL} from 'node:url';

const EXPECTED = '9c8455bd7ee1134f87277c72aaa92c6d493e44c2adf4ac4abfe9c76813f1ae0a';
const EVENTS = new Set(['source_model_identity_verified', 'native_shard_download_started',
  'native_shard_completed', 'native_shard_revalidated', 'native_reconstruction_complete',
  'native_reconstruction_failed', 'vllm_cli_preflight', 'confidential_gpu_comms_preflight',
  'gpu_comms_preflight_failed']);
const NUMBERS = ['shard', 'total_shards', 'bytes', 'files', 'replaced_tensors',
  'replaced_tensor_count', 'maximum_projection_relative_rms_error',
  'numerical_maximum_relative_rms_error', 'world_size'];
const COMPONENTS = ['model-download', 'model-perms', 'model-measurer', 'attest-proxy',
  'sglang', 'runtime-capacity-check', 'q2-native-reconstruction', 'vllm-communication-preflight'];

export function summarize(raw) {
  const timestamps = raw.match(/2026-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z/g) || [];
  const result = {last_timestamp: timestamps.sort().at(-1) || null, events: [],
    unsupported_operation_errors: (raw.match(/CUDA error: operation not supported/gi) || []).length,
    allocation_errors: (raw.match(/failed to allocate|out of memory|failed to reserve/gi) || []).length,
    raw_logs_persisted: false};
  for (const line of raw.split('\n')) {
    const start = line.indexOf('{');
    if (start < 0) continue;
    let input;
    try { input = JSON.parse(line.slice(start)); } catch { continue; }
    if (!EVENTS.has(input?.event)) continue;
    const event = {event: input.event};
    for (const field of NUMBERS) {
      if (typeof input[field] === 'number' && Number.isFinite(input[field])) event[field] = input[field];
    }
    if (typeof input.passed === 'boolean') event.passed = input.passed;
    if (['ValueError', 'RuntimeError', 'TimeoutExpired', 'OSError', 'MemoryError'].includes(input.error_class)) {
      event.error_class = input.error_class;
    }
    if (input.event === 'confidential_gpu_comms_preflight') {
      event.verified_ranks = Array.isArray(input.reports) ? input.reports.filter(r =>
        Number.isInteger(r.rank) && r.rank >= 0 && r.rank < 8 && r.verified === true).map(r => r.rank) : [];
      event.average_4MiB_allreduce_milliseconds = Array.isArray(input.reports) ? input.reports.map(r =>
        r.average_4MiB_allreduce_milliseconds).filter(n => typeof n === 'number' && Number.isFinite(n)) : [];
      event.failed_ranks = Array.isArray(input.failures) ? input.failures.filter(r =>
        Number.isInteger(r.rank) && r.rank >= 0 && r.rank < 8).map(r => ({rank: r.rank,
          unsupported_operation_observed: r.unsupported_operation_observed === true,
          out_of_memory_observed: r.out_of_memory_observed === true})) : [];
    }
    result.events.push(event);
  }
  result.events = result.events.slice(-100);
  const measured = raw.match(/model artifact measured: files=(\d+) bytes=(\d+) digest=(sha256:[a-f0-9]{64})/g)?.at(-1);
  if (measured) {
    const m = measured.match(/files=(\d+) bytes=(\d+) digest=(sha256:[a-f0-9]{64})/);
    result.measurement = {files: Number(m[1]), bytes: Number(m[2]), digest: m[3]};
  }
  return result;
}

function logs(component, cvm) {
  return new Promise(resolve => {
    const child = spawn('phala', ['logs', 'dstack-' + component + '-1', '--cvm-id', cvm,
      '--tail', '150', '--stderr', '--timestamps', '--json'], {stdio: ['ignore', 'pipe', 'pipe']});
    let stdout = '', bytes = 0, overflow = false, completed = false;
    const timer = setTimeout(() => child.kill(), 45000); // Only this local read process.
    child.stdout.on('data', b => {
      bytes += b.length;
      if (bytes > 2 * 1024 * 1024) { overflow = true; child.kill(); }
      else stdout += b.toString();
    });
    child.stderr.on('data', () => {}); // SDK/CLI diagnostics can contain private payloads.
    function finish() {
      if (completed) return;
      completed = true; clearTimeout(timer);
      if (!overflow) {
        try {
          const parsed = JSON.parse(stdout);
          if (parsed.success && typeof parsed.logs === 'string') {
            resolve({available: true, ...summarize(parsed.logs)}); return;
          }
        } catch {}
      }
      resolve({available: false, private_diagnostics_withheld: true});
    }
    child.once('error', finish); child.once('close', finish);
  });
}

async function main() {
  const args = process.argv.slice(2);
  const argument = name => { const i = args.indexOf(name); if (i < 0 || !args[i + 1]) throw new Error(); return args[i + 1]; };
  const sdk = await import(pathToFileURL(argument('--sdk')).href);
  const credentials = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.phala-cloud/credentials.json'), 'utf8'));
  const profile = credentials.profiles[credentials.current_profile];
  const client = sdk.createClient({apiKey: profile.token, baseURL: 'https://cloud-api.phala.com/api/v1',
    version: '2026-06-23', timeout: 30000});
  const id = 'cvm_BKyN16wl';
  const results = await Promise.allSettled([sdk.safeGetCvmInfo(client, {id}),
    sdk.safeGetCvmContainersStats(client, {id}), sdk.safeGetCvmStats(client, {id})]);
  const data = i => results[i].status === 'fulfilled' && results[i].value.success ? results[i].value.data : null;
  const cvm = data(0), containers = data(1)?.containers, sys = data(2)?.sysinfo;
  const report = {observed_at_utc: new Date().toISOString(), cvm_mutations: 0,
    inference_prompts_sent: 0, provider: null, components: {},
    native_conversion_gpu_startup_speed_or_quality_not_inferred_from_provider_status: true};
  if (cvm) {
    const digest = crypto.createHash('sha256').update(cvm.compose_file.docker_compose_file).digest('hex');
    report.provider = {status: cvm.status, in_progress: cvm.in_progress, compose_hash: cvm.compose_hash,
      docker_compose_sha256: digest, expected_staging_configuration: digest === EXPECTED};
  }
  if (sys) report.host_resources = {num_cpus: sys.num_cpus, available_memory: sys.available_memory,
    loadavg: [sys.loadavg_one, sys.loadavg_five, sys.loadavg_fifteen],
    disks: sys.disks.map(d => ({total_size: d.total_size, free_size: d.free_size})), gpu_metrics_available: false};
  // Container observation may be temporarily unavailable during boot. That is
  // not permission to restart, update again, or infer terminal failure.
  if (containers && report.provider?.expected_staging_configuration) {
    for (const name of COMPONENTS) {
      const container = containers.find(c => c.names.includes('/dstack-' + name + '-1'));
      if (!container) continue;
      report.components[name] = {state: container.state, status: container.status};
    }
    const active = ['q2-native-reconstruction', 'vllm-communication-preflight', 'model-measurer', 'sglang']
      .filter(n => report.components[n] && report.components[n].state !== 'created');
    await Promise.all(active.map(async name => { report.components[name].logs = await logs(name, id); }));
  }
  fs.writeFileSync(argument('--report'), JSON.stringify(report, null, 2) + '\n', {mode: 0o600});
  console.log(JSON.stringify(report));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(() => { console.error('Read-only stage observation failed; private diagnostics withheld'); process.exitCode = 1; });
}
