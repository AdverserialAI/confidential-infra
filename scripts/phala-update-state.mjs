// State guard only. This module performs no I/O and grants no owner approval.
export const CPU_FIX_SOURCE = '9c8455bd7ee1134f87277c72aaa92c6d493e44c2adf4ac4abfe9c76813f1ae0a';
export function allowedUpdateState(cvm, report, stats, reviewedStartupError = false) {
  if (cvm.in_progress !== false) return false;
  if (!reviewedStartupError) return cvm.status === 'running';
  return cvm.status === 'error' &&
    report.kind === 'reviewed_staging_cpu_quota_correction_only' &&
    report.source_sha256 === CPU_FIX_SOURCE &&
    report.removed_converter_cpu_quota === '8.0' &&
    report.converter_memory_limit_unchanged === '16g' &&
    report.serving_runtime_unchanged === true &&
    stats?.in_progress === false && stats?.status === 'stopped' &&
    stats?.boot_error === 'failed to start containers';
}
