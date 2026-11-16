#!/usr/bin/env node
// Read-only sanitized resource/rental observation. No private payload output.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

async function main() {
  const args = process.argv.slice(2);
  const arg = name => {const i = args.indexOf(name); if (i < 0 || !args[i + 1]) throw new Error(); return args[i + 1];};
  const sdk = await import(pathToFileURL(arg('--sdk')).href);
  const credentials = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.phala-cloud/credentials.json'), 'utf8'));
  const profile = credentials.profiles[credentials.current_profile];
  const client = sdk.createClient({apiKey: profile.token, baseURL: 'https://cloud-api.phala.com/api/v1',
    version: '2026-06-23', timeout: 30000});
  const result = await sdk.safeGetCvmInfo(client, {id: 'cvm_BKyN16wl'});
  if (!result.success) throw new Error();
  const cvm = result.data, rental = cvm.gpu_rental_order;
  const report = {observed_at_utc: new Date().toISOString(), cvm_mutations: 0,
    provider_status: cvm.status, in_progress: cvm.in_progress, project_type: cvm.project_type,
    cvm_record_present: true, node_info_present: !!cvm.node_info, resources: {}, rental: null,
    physical_gpu_allocation_not_independently_proven: true};
  for (const key of ['vcpu', 'memory_in_gb', 'disk_in_gb', 'gpus']) {
    if (Number.isFinite(cvm.resource?.[key])) report.resources[key] = cvm.resource[key];
  }
  if (rental) {
    report.rental = {status: rental.status, pricing_plan: rental.pricing_plan,
      completed_at: rental.completed_at, cancelled_at: rental.cancelled_at, decoupled_at: rental.decoupled_at};
  }
  fs.writeFileSync(arg('--report'), JSON.stringify(report, null, 2) + '\n', {mode: 0o600});
  console.log(JSON.stringify(report));
}
main().catch(() => {console.error('Read-only resource/rental observation failed; private diagnostics withheld'); process.exitCode = 1;});
