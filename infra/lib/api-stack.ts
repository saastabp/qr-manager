/**
 * Api stack: the code table, the render service, and the HTTP API.
 *
 * A short parent that assembles the constructs in lib/api/. See docs/DESIGN.md, section 5.
 */
import { Stack, StackProps } from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as sns from 'aws-cdk-lib/aws-sns';
import { Construct } from 'constructs';
import { CodeTable } from './api/code-table';
import { HttpApi } from './api/http-api';
import { RenderService } from './api/render-service';
import { EnvConfig } from './config';

/** Inputs to this stack. The resources come from the Edge and Auth stacks. */
export interface ApiStackProps extends StackProps {
  readonly config: EnvConfig;
  readonly keyValueStore: cloudfront.KeyValueStore;
  readonly alertTopic: sns.Topic;
  readonly userPool: cognito.UserPool;
  readonly cliClient: cognito.UserPoolClient;
}

export class ApiStack extends Stack {
  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);
    const { config } = props;

    const codes = new CodeTable(this, 'Codes', { config });
    const render = new RenderService(this, 'Render', { config });

    new HttpApi(this, 'Http', {
      config,
      table: codes.table,
      imagesBucket: render.bucket,
      renderFunction: render.renderFunction,
      keyValueStore: props.keyValueStore,
      alertTopic: props.alertTopic,
      userPool: props.userPool,
      cliClient: props.cliClient,
    });
  }
}