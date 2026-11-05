/**
 * Scan log format: shared by the stack that writes logs and the stack that reads them.
 *
 * Edge tells CloudFront what to write and where, and Analytics describes those same files
 * to Athena. If the two disagreed, queries would quietly return nothing.
 */

/** Folder in the log bucket that CloudFront writes to. */
export const LOG_PREFIX = 'qr-logs';

/** Athena column names and the log fields they read, from a real delivered log file. */
export const LOG_COLUMNS: Record<string, string> = {
  timestamp_ms: 'timestamp(ms)',
  request_id: 'x-edge-request-id',
  client_ip: 'c-ip',
  uri_stem: 'cs-uri-stem',
  status: 'sc-status',
  user_agent: 'cs(User-Agent)',
  country: 'c-country',
  result_type: 'x-edge-result-type',
  time_taken: 'time-taken',
  outcome_data: 'viewer-request-log-data',
};