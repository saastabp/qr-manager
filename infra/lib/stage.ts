/**
 * One complete copy of QR Manager: every stack, for one environment.
 *
 * bin/app.ts creates this once per environment. Keeping the stacks in a Stage also means
 * a deployment pipeline can be added later without renaming or replacing anything. See
 * docs/DESIGN.md, section 5.
 */
import { Stage, StageProps, Tags } from 'aws-cdk-lib';
import { Construct } from 'constructs';
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
    new EdgeStack(this, 'Edge', {
      stackName: resourceName(config, 'Edge'),
      config,
    });

    // Applied to every taggable resource, for filtering and cost tracking.
    Tags.of(this).add('app', APP_NAME);
    Tags.of(this).add('environment', config.envName);
    Tags.of(this).add('managed-by', 'cdk');
  }
}