/**
 * One complete copy of QR Manager: every stack, for one environment.
 *
 * bin/app.ts creates this once per environment. Keeping the stacks in a Stage also means
 * a deployment pipeline can be added later without renaming or replacing anything. See
 * docs/DESIGN.md, section 5.
 */
import { Stage, StageProps, Tags } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { AnalyticsStack } from './analytics-stack';
import { ApiStack } from './api-stack';
import { AuthStack } from './auth-stack';
import { APP_NAME, EnvConfig, resourceName } from './config';
import { EdgeStack } from './edge-stack';

/** Inputs to this stage. StageProps supplies the account and region for its stacks. */
export interface QrManagerStageProps extends StageProps {
  readonly config: EnvConfig;
}

export class QrManagerStage extends Stage {
  constructor(scope: Construct, id: string, props: QrManagerStageProps) {
    super(scope, id, props);
    const { config } = props;

    // stackName is set explicitly. Inside a Stage, the default name would include the
    // stage, and renaming a deployed stack makes CloudFormation treat it as a new one.
    const edge = new EdgeStack(this, 'Edge', {
      stackName: resourceName(config, 'Edge'),
      config,
    });

    const auth = new AuthStack(this, 'Auth', {
      stackName: resourceName(config, 'Auth'),
      config,
    });

    // Passing resources between stacks creates cross-stack references, so CDK deploys
    // Edge and Auth before this one.
    new ApiStack(this, 'Api', {
      stackName: resourceName(config, 'Api'),
      config,
      keyValueStore: edge.keyValueStore,
      alertTopic: edge.alertTopic,
      userPool: auth.userPool,
      cliClient: auth.cliClient,
    });

    new AnalyticsStack(this, 'Analytics', {
      stackName: resourceName(config, 'Analytics'),
      config,
      logBucket: edge.logBucket,
    });

    // Applied to every taggable resource, for filtering and cost tracking.
    Tags.of(this).add('app', APP_NAME);
    Tags.of(this).add('environment', config.envName);
    Tags.of(this).add('managed-by', 'cdk');
  }
}