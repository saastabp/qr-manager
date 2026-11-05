/**
 * Scan logging: records every request to S3 for later analysis.
 *
 * Creates the log bucket and CloudFront standard logging v2. Delivery to S3 is free, and
 * logs start as soon as this deploys, because scans that are not logged cannot be
 * recovered. See docs/DESIGN.md, section 5.
 */
import { Duration, RemovalPolicy, Stack } from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { EnvConfig, resourceName } from '../config';

/** Folder in the log bucket that CloudFront writes to. */
const LOG_PREFIX = 'qr-logs';

/** Scan logs are kept this long, then S3 deletes them. They contain raw viewer IPs. */
const LOG_RETENTION_DAYS = 400;

/** Fields written to each scan log line. */
const LOG_FIELDS = [
  'timestamp(ms)',
  'x-edge-request-id',
  'c-ip',
  'cs-uri-stem',
  'sc-status',
  'cs(User-Agent)',
  'c-country',
  'x-edge-result-type',
  'time-taken',
  'viewer-request-log-data',
];

/** Inputs to this construct. */
export interface ScanLoggingProps {
  readonly config: EnvConfig;
  /** The distribution whose requests are logged. */
  readonly distribution: cloudfront.Distribution;
}

export class ScanLogging extends Construct {
  // Public because the Analytics stack will query this bucket.
  readonly bucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: ScanLoggingProps) {
    super(scope, id);
    const { config, distribution } = props;
    const { account, region } = Stack.of(this);

    // --- Log bucket ---------------------------------------------------------------
    this.bucket = new s3.Bucket(this, 'Bucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: config.retainData ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
      // Sandbox only: empties the bucket so the stack can be deleted.
      autoDeleteObjects: !config.retainData,
      lifecycleRules: [{ expiration: Duration.days(LOG_RETENTION_DAYS) }],
    });

    // Lets the log delivery service write to the bucket, for this account only.
    this.bucket.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: 'AllowLogDelivery',
        principals: [new iam.ServicePrincipal('delivery.logs.amazonaws.com')],
        actions: ['s3:PutObject'],
        resources: [this.bucket.arnForObjects(`${LOG_PREFIX}/*`)],
        conditions: {
          StringEquals: {
            's3:x-amz-acl': 'bucket-owner-full-control',
            'aws:SourceAccount': account,
          },
          ArnLike: {
            'aws:SourceArn': `arn:aws:logs:${region}:${account}:delivery-source:*`,
          },
        },
      }),
    );

    // --- Log delivery -------------------------------------------------------------
    // Standard logging v2 takes three parts. These are L1 constructs: direct mappings
    // of CloudFormation resources, used here because CDK has no higher-level version.

    // The source says what to log.
    const sourceName = resourceName(config, 'access-logs');
    const deliverySource = new logs.CfnDeliverySource(this, 'Source', {
      name: sourceName,
      logType: 'ACCESS_LOGS',
      resourceArn: distribution.distributionArn,
    });

    // The destination says where. The output format cannot be changed after creation.
    const deliveryDestination = new logs.CfnDeliveryDestination(this, 'Destination', {
      name: resourceName(config, 'access-logs-s3'),
      outputFormat: 'json',
      destinationResourceArn: `${this.bucket.bucketArn}/${LOG_PREFIX}`,
    });

    // The delivery connects the two, and picks the fields and folder layout.
    const delivery = new logs.CfnDelivery(this, 'Delivery', {
      deliverySourceName: sourceName,
      deliveryDestinationArn: deliveryDestination.attrArn,
      recordFields: LOG_FIELDS,
      s3SuffixPath: '{yyyy}/{MM}/{dd}',
      s3EnableHiveCompatiblePath: true,
    });

    // The name above is plain text, so CDK cannot see these dependencies by itself.
    delivery.addDependency(deliverySource);
    delivery.addDependency(deliveryDestination);
    if (this.bucket.policy) {
      delivery.node.addDependency(this.bucket.policy);
    }
  }
}