/**
 * Edge stack: everything a printed QR code depends on.
 *
 * A short parent that assembles the constructs in lib/edge/. It is separate from the API
 * so routine API deploys can never touch the path that serves scans. See docs/DESIGN.md,
 * section 5.
 */
import { CfnOutput, Stack, StackProps } from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as sns from 'aws-cdk-lib/aws-sns';
import { Construct } from 'constructs';
import { EnvConfig } from './config';
import { Alerts } from './edge/alerts';
import { RedirectService } from './edge/redirect-service';
import { ScanLogging } from './edge/scan-logging';

/** Inputs to this stack. Extending StackProps adds the standard account and region. */
export interface EdgeStackProps extends StackProps {
  readonly config: EnvConfig;
}

export class EdgeStack extends Stack {
  // Public so other stacks can use them. CDK turns these into cross-stack references.
  readonly keyValueStore: cloudfront.KeyValueStore;
  readonly logBucket: s3.Bucket;
  readonly alertTopic: sns.Topic;

  constructor(scope: Construct, id: string, props: EdgeStackProps) {
    super(scope, id, props);
    const { config } = props;

    // The scan path, then logging and alerts, which both build on it.
    const redirect = new RedirectService(this, 'Redirect', { config });
    const scanLogging = new ScanLogging(this, 'ScanLogging', {
      config,
      distribution: redirect.distribution,
    });
    const alerts = new Alerts(this, 'Alerts', {
      config,
      functionLogs: redirect.functionLogs,
    });

    this.keyValueStore = redirect.keyValueStore;
    this.logBucket = scanLogging.bucket;
    this.alertTopic = alerts.topic;

    // Printed after a deploy, and useful for the smoke test.
    new CfnOutput(this, 'RedirectUrl', { value: `https://${config.domainName}/` });
    new CfnOutput(this, 'KeyValueStoreArn', { value: this.keyValueStore.keyValueStoreArn });
    new CfnOutput(this, 'DistributionId', { value: redirect.distribution.distributionId });
    new CfnOutput(this, 'LogBucketName', { value: this.logBucket.bucketName });
  }
}