/**
 * Tests for the Edge stack's generated CloudFormation.
 *
 * Guards the settings that matter most: prod data surviving a stack deletion, scan
 * logging, and redirects never being cached.
 */
import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { EnvConfig, PROD, SANDBOX } from '../lib/config';
import { EdgeStack } from '../lib/edge-stack';
import { QrManagerStage } from '../lib/stage';

const ENV = { account: '111111111111', region: 'us-east-1' };

/** The AWS managed cache policy id for CachingDisabled. */
const CACHING_DISABLED = '4135ea2d-6df8-44a3-9df3-4b5a84be39ad';

/** Build the Edge stack the same way the real app does. */
function edgeStack(config: EnvConfig): EdgeStack {
  const stage = new QrManagerStage(new App(), config.envName, { env: ENV, config });
  return stage.node.findChild('Edge') as EdgeStack;
}

describe.each([
  ['sandbox', SANDBOX],
  ['prod', PROD],
])('Edge stack in %s', (_name, config) => {
  const stack = edgeStack(config);
  const template = Template.fromStack(stack);

  it('has an explicit stack name', () => {
    expect(stack.stackName).toBe(`qr-manager-${config.envName}-Edge`);
  });

  it('serves the configured domain with redirects uncached', () => {
    template.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        Aliases: [config.domainName],
        DefaultCacheBehavior: Match.objectLike({
          CachePolicyId: CACHING_DISABLED,
          ViewerProtocolPolicy: 'redirect-to-https',
          FunctionAssociations: [Match.objectLike({ EventType: 'viewer-request' })],
        }),
      }),
    });
  });

  it('uses the runtime that supports KeyValueStore', () => {
    template.hasResourceProperties('AWS::CloudFront::Function', {
      FunctionConfig: Match.objectLike({ Runtime: 'cloudfront-js-2.0' }),
    });
  });

  it('delivers scan logs as JSON under the agreed prefix', () => {
    template.hasResourceProperties('AWS::Logs::DeliveryDestination', { OutputFormat: 'json' });
    template.hasResourceProperties('AWS::Logs::Delivery', {
      S3EnableHiveCompatiblePath: true,
      S3SuffixPath: '{yyyy}/{MM}/{dd}',
      RecordFields: Match.arrayWith(['cs-uri-stem', 'viewer-request-log-data']),
    });
  });

  it('expires scan logs after 400 days', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      LifecycleConfiguration: {
        Rules: [Match.objectLike({ ExpirationInDays: 400, Status: 'Enabled' })],
      },
    });
  });

  it('keeps the log bucket private', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
    });
  });

  it('alarms on fallback outcomes', () => {
    template.hasResourceProperties('AWS::Logs::MetricFilter', {
      FilterPattern: '"\\"outcome\\":\\"fallback\\""',
    });
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      Threshold: 5,
      TreatMissingData: 'notBreaching',
    });
  });
});

describe('environment differences', () => {
  const prod = Template.fromStack(edgeStack(PROD));
  const sandbox = Template.fromStack(edgeStack(SANDBOX));

  it('retains prod data when the stack is deleted', () => {
    prod.hasResource('AWS::CloudFront::KeyValueStore', { DeletionPolicy: 'Retain' });
    prod.hasResource('AWS::S3::Bucket', { DeletionPolicy: 'Retain' });
  });

  it('keeps sandbox disposable', () => {
    sandbox.hasResource('AWS::CloudFront::KeyValueStore', { DeletionPolicy: 'Delete' });
    sandbox.hasResource('AWS::S3::Bucket', { DeletionPolicy: 'Delete' });
  });

  it('emails alarms from prod only', () => {
    prod.resourceCountIs('AWS::SNS::Subscription', 1);
    sandbox.resourceCountIs('AWS::SNS::Subscription', 0);
  });
});