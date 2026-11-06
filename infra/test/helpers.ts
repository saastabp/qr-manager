/**
 * Shared test setup: builds stacks the way the real app does.
 *
 * Tests use a fake account, so they never depend on real AWS. The bundling context tells
 * CDK to skip Lambda bundling, which is slow and which these tests do not need.
 */
import { App, Stack } from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { EnvConfig } from '../lib/config';
import { QrManagerStage } from '../lib/stage';

const ENV = { account: '111111111111', region: 'us-east-1' };

/** Build one environment and return one of its stacks, such as 'Edge'. */
export function buildStack<T extends Stack = Stack>(config: EnvConfig, stackId: string): T {
  const app = new App({ context: { 'aws:cdk:bundling-stacks': [] } });
  const stage = new QrManagerStage(app, config.envName, { env: ENV, config });
  return stage.node.findChild(stackId) as T;
}

/** Return the CloudFormation template CDK generates for one stack. */
export function stackTemplate(config: EnvConfig, stackId: string): Template {
  return Template.fromStack(buildStack(config, stackId));
}