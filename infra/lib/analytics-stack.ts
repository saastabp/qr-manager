/**
 * Analytics stack: lets scan logs be queried with SQL.
 *
 * A Glue table describes the JSON files in the log bucket, and Athena queries them in
 * place. It is separate so schema fixes never touch the Edge stack. See docs/DESIGN.md,
 * section 5.
 */
import { CfnOutput, Stack, StackProps } from 'aws-cdk-lib';
import * as athena from 'aws-cdk-lib/aws-athena';
import * as glue from 'aws-cdk-lib/aws-glue';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { EnvConfig } from './config';

/** Column names and the log fields they read, from a real delivered log file. */
const COLUMNS: Record<string, string> = {
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

/** The first year Athena looks for logs, and how far ahead it looks. */
const FIRST_LOG_YEAR = 2026;
const LAST_LOG_YEAR = 2046;

/** Stops a single query reading more than this, as a guard against mistakes. */
const MAX_BYTES_PER_QUERY = 100 * 1024 * 1024;

/** Inputs to this stack. */
export interface AnalyticsStackProps extends StackProps {
  readonly config: EnvConfig;
  readonly logBucket: s3.Bucket;
}

export class AnalyticsStack extends Stack {
  constructor(scope: Construct, id: string, props: AnalyticsStackProps) {
    super(scope, id, props);
    const { config, logBucket } = props;

    // Athena names cannot contain hyphens without quoting, so use underscores here.
    const databaseName = `qr_manager_${config.envName}`;
    const logLocation = `s3://${logBucket.bucketName}/qr-logs`;

    // --- Glue database and table --------------------------------------------------
    const database = new glue.CfnDatabase(this, 'Database', {
      catalogId: this.account,
      databaseInput: { name: databaseName },
    });

    // Every value in the logs is a string, including numbers.
    const mappings = Object.fromEntries(
      Object.entries(COLUMNS).map(([column, field]) => [`mapping.${column}`, field]),
    );

    const table = new glue.CfnTable(this, 'ScansTable', {
      catalogId: this.account,
      databaseName,
      tableInput: {
        name: 'scans',
        tableType: 'EXTERNAL_TABLE',
        partitionKeys: [
          { name: 'year', type: 'string' },
          { name: 'month', type: 'string' },
          { name: 'day', type: 'string' },
        ],
        parameters: {
          classification: 'json',
          // Partition projection: Athena works out the folders, so nothing registers them.
          'projection.enabled': 'true',
          'projection.year.type': 'integer',
          'projection.year.range': `${FIRST_LOG_YEAR},${LAST_LOG_YEAR}`,
          'projection.month.type': 'integer',
          'projection.month.range': '1,12',
          'projection.month.digits': '2',
          'projection.day.type': 'integer',
          'projection.day.range': '1,31',
          'projection.day.digits': '2',
          'storage.location.template': `${logLocation}/year=\${year}/month=\${month}/day=\${day}`,
        },
        storageDescriptor: {
          location: `${logLocation}/`,
          inputFormat: 'org.apache.hadoop.mapred.TextInputFormat',
          outputFormat: 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat',
          columns: Object.keys(COLUMNS).map((name) => ({ name, type: 'string' })),
          serdeInfo: {
            serializationLibrary: 'org.openx.data.jsonserde.JsonSerDe',
            parameters: mappings,
          },
        },
      },
    });
    table.addDependency(database);

    // --- Athena workgroup ---------------------------------------------------------
    new athena.CfnWorkGroup(this, 'WorkGroup', {
      // Sandbox and prod share an account, where workgroup names must be unique.
      name: `qr_analytics_${config.envName}`,
      // Lets the stack be deleted even when the workgroup has saved query history.
      recursiveDeleteOption: true,
      workGroupConfiguration: {
        enforceWorkGroupConfiguration: true,
        bytesScannedCutoffPerQuery: MAX_BYTES_PER_QUERY,
        resultConfiguration: {
          outputLocation: `s3://${logBucket.bucketName}/athena-results/`,
        },
      },
    });

    new CfnOutput(this, 'DatabaseName', { value: databaseName });
  }
}