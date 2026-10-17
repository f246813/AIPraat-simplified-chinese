// Adapted from openai/codex exec_cell/compact.rs, Apache-2.0.
// Revision ab45264919aaeb8a421cc156f1a5459ef9d60b72.
// compact_command_lines chooses failure before active, then completed.
// Praat supplies persisted statuses rather than shell exit codes/start_time.
export function activityState(failed: boolean, active: boolean) {
  return failed ? 'failed' : active ? 'running' : 'complete';
}
