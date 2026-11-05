/**
 * HTTP API: the API Lambda, its routes, and its failure alarm.
 *
 * Every route needs a valid Cognito token, checked by API Gateway before the Lambda runs.
 * There is no DELETE route, which enforces the rule that codes are never deleted. See
 * docs/DESIGN.md, section 7.
 */
import { CfnOutput, Duration, RemovalPolicy } from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpUserPoolAuthorizer } from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import { HttpLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatchActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as sns from 'aws-cdk-lib/aws-sns';
import { Construct } from 'constructs';
import { EnvConfig, resourceName } from '../config';
import { LAMBDA_ARCH, PYTHON_RUNTIME, pythonBundle } from '../python-lambda';

/** Inputs to this construct. */
export interface HttpApiProps {
  readonly config: EnvConfig;
  readonly table: dynamodb.Table;
  readonly imagesBucket: s3.Bucket;
  readonly renderFunction: lambda.Function;
  readonly keyValueStore: cloudfront.KeyValueStore;
  readonly userPool: cognito.UserPool;
  readonly cliClient: cognito.UserPoolClient;
  readonly alertTopic: sns.Topic;
}

export class HttpApi extends Construct {
  constructor(scope: Construct, id: string, props: HttpApiProps) {
    super(scope, id);
    const { config } = props;

    // --- API Lambda ---------------------------------------------------------------
    const functionName = resourceName(config, 'api');
    const logGroup = new logs.LogGroup(this, 'Logs', {
      logGroupName: `/aws/lambda/${functionName}`,
      retention: config.logRetention,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const apiFunction = new lambda.Function(this, 'Function', {
      functionName,
      runtime: PYTHON_RUNTIME,
      architecture: LAMBDA_ARCH,
      handler: 'api_handler.lambda_handler',
      code: pythonBundle('backend'),
      memorySize: 256,
      // Long enough for a render, and shorter than API Gateway's 30 second limit.
      timeout: Duration.seconds(25),
      // How config.ts values reach the Lambda at runtime. See backend/src/bootstrap.py.
      environment: {
        CODES_TABLE: props.table.tableName,
        KVS_ARN: props.keyValueStore.keyValueStoreArn,
        IMAGES_BUCKET: props.imagesBucket.bucketName,
        RENDER_FUNCTION: props.renderFunction.functionName,
        REDIRECT_DOMAIN: config.domainName,
        POWERTOOLS_SERVICE_NAME: functionName,
      },
      logGroup,
    });

    // --- Permissions --------------------------------------------------------------
    // Only the calls the adapter makes. Leaving out DeleteItem backs up the rule that codes
    // are never deleted. A CDK grant covers the table and its indexes.
    props.table.grant(
      apiFunction,
      'dynamodb:GetItem',
      'dynamodb:Query',
      'dynamodb:PutItem',
      'dynamodb:UpdateItem',
    );
    // Includes s3:ListBucket. Without it, S3 reports a missing PNG as 403 rather than 404.
    props.imagesBucket.grantRead(apiFunction);
    props.renderFunction.grantInvoke(apiFunction);
    apiFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'cloudfront-keyvaluestore:DescribeKeyValueStore',
          'cloudfront-keyvaluestore:PutKey',
        ],
        resources: [props.keyValueStore.keyValueStoreArn],
      }),
    );

    // --- HTTP API -----------------------------------------------------------------
    const api = new apigateway.HttpApi(this, 'Api', {
      apiName: functionName,
      // Applies to every route below, so no route can be left unprotected by accident.
      defaultAuthorizer: new HttpUserPoolAuthorizer('Authorizer', props.userPool, {
        userPoolClients: [props.cliClient],
      }),
    });

    const integration = new HttpLambdaIntegration('Integration', apiFunction);
    api.addRoutes({
      path: '/codes',
      methods: [apigateway.HttpMethod.GET, apigateway.HttpMethod.POST],
      integration,
    });
    api.addRoutes({
      path: '/codes/{code}',
      methods: [apigateway.HttpMethod.GET, apigateway.HttpMethod.PATCH],
      integration,
    });
    api.addRoutes({
      path: '/codes/{code}/image',
      methods: [apigateway.HttpMethod.GET],
      integration,
    });

    // --- Failure alarm ------------------------------------------------------------
    // Any failed publish or render means a code is stuck in pending.
    const failureFilter = logGroup.addMetricFilter('StepFailureFilter', {
      filterPattern: logs.FilterPattern.anyTerm('Publish failed', 'Render failed'),
      metricNamespace: 'QrManager',
      metricName: `${config.envName}-step-failures`,
      metricValue: '1',
    });

    const failureAlarm = new cloudwatch.Alarm(this, 'StepFailureAlarm', {
      alarmName: resourceName(config, 'step-failures'),
      alarmDescription: 'A publish or render failed, so a code is stuck in pending',
      metric: failureFilter.metric({ statistic: 'Sum', period: Duration.minutes(5) }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    failureAlarm.addAlarmAction(new cloudwatchActions.SnsAction(props.alertTopic));

    // --- Outputs ------------------------------------------------------------------
    new CfnOutput(this, 'ApiUrl', { value: api.apiEndpoint });
  }
}