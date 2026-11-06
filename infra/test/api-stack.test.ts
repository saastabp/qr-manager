/**
 * Tests for the Auth and Api stacks' generated CloudFormation.
 *
 * Guards authentication on every route, the no-delete rule, prod data protection, and
 * least privilege for the render Lambda.
 */
import { Match } from 'aws-cdk-lib/assertions';
import { PROD, SANDBOX } from '../lib/config';
import { stackTemplate as templateFor } from './helpers';

describe('API routes', () => {
  const routes = Object.values(
    templateFor(SANDBOX, 'Api').findResources('AWS::ApiGatewayV2::Route'),
  );

  it('match the API contract exactly, with no DELETE', () => {
    const keys = routes.map((route) => route.Properties.RouteKey).sort();

    expect(keys).toEqual([
      'GET /codes',
      'GET /codes/{code}',
      'GET /codes/{code}/image',
      'PATCH /codes/{code}',
      'POST /codes',
    ]);
  });

  it('all require a token', () => {
    for (const route of routes) {
      expect(route.Properties.AuthorizationType).toBe('JWT');
    }
  });
});

describe('Lambda functions', () => {
  const template = templateFor(SANDBOX, 'Api');

  it.each([
    ['qr-manager-sandbox-api', 256, 25],
    ['qr-manager-sandbox-render', 1024, 10],
  ])('%s uses the agreed runtime and sizing', (name, memory, timeout) => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: name,
      Runtime: 'python3.14',
      Architectures: ['arm64'],
      MemorySize: memory,
      Timeout: timeout,
    });
  });

  it('gives the API Lambda everything bootstrap.py reads', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'qr-manager-sandbox-api',
      Environment: {
        Variables: Match.objectLike({
          CODES_TABLE: Match.anyValue(),
          KVS_ARN: Match.anyValue(),
          IMAGES_BUCKET: Match.anyValue(),
          RENDER_FUNCTION: Match.anyValue(),
          REDIRECT_DOMAIN: 'qr-sandbox.360balancedliving.com',
        }),
      },
    });
  });

  it('lets the API Lambda write to KVS', () => {
    template.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: [
              'cloudfront-keyvaluestore:DescribeKeyValueStore',
              'cloudfront-keyvaluestore:PutKey',
            ],
          }),
        ]),
      },
    });
  });

  it('gives the API Lambda only the table calls it makes, and never a delete', () => {
    template.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: [
              'dynamodb:GetItem',
              'dynamodb:Query',
              'dynamodb:PutItem',
              'dynamodb:UpdateItem',
            ],
          }),
        ]),
      },
    });

    const policies = JSON.stringify(template.findResources('AWS::IAM::Policy'));
    expect(policies).not.toContain('dynamodb:DeleteItem');
    expect(policies).toContain('/index/*');
  });

  it('lets the API Lambda list the images bucket, so a missing PNG reads as 404', () => {
    const policies = JSON.stringify(template.findResources('AWS::IAM::Policy'));

    expect(policies).toContain('s3:List*');
  });

  it('keeps the render Lambda away from DynamoDB and KVS', () => {
    const policies = Object.values(template.findResources('AWS::IAM::Policy'));
    const renderPolicies = policies.filter((policy) =>
      JSON.stringify(policy.Properties.Roles).includes('RenderFunction'),
    );

    expect(renderPolicies).toHaveLength(1);
    expect(JSON.stringify(renderPolicies)).not.toContain('dynamodb');
    expect(JSON.stringify(renderPolicies)).not.toContain('keyvaluestore');
  });

  it('alarms on any failed publish or render', () => {
    template.hasResourceProperties('AWS::Logs::MetricFilter', {
      FilterPattern: Match.stringLikeRegexp('Publish failed.*Render failed'),
    });
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'qr-manager-sandbox-step-failures',
      Threshold: 1,
      TreatMissingData: 'notBreaching',
      AlarmActions: [Match.anyValue()],
    });
  });
});

describe('code table', () => {
  it('has the index the adapter queries', () => {
    templateFor(SANDBOX, 'Api').hasResourceProperties('AWS::DynamoDB::Table', {
      GlobalSecondaryIndexes: [
        Match.objectLike({
          IndexName: 'by-owner',
          KeySchema: [
            { AttributeName: 'owner_sub', KeyType: 'HASH' },
            { AttributeName: 'created_at', KeyType: 'RANGE' },
          ],
        }),
      ],
    });
  });

  it('is fully protected in prod', () => {
    templateFor(PROD, 'Api').hasResource('AWS::DynamoDB::Table', {
      DeletionPolicy: 'Retain',
      Properties: Match.objectLike({
        DeletionProtectionEnabled: true,
        PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true },
      }),
    });
  });

  it('stays disposable in sandbox', () => {
    templateFor(SANDBOX, 'Api').hasResource('AWS::DynamoDB::Table', {
      DeletionPolicy: 'Delete',
    });
  });
});

describe('user pool', () => {
  const template = templateFor(PROD, 'Auth');

  it('is invite-only and sends mail through SES', () => {
    template.hasResourceProperties('AWS::Cognito::UserPool', {
      AdminCreateUserConfig: { AllowAdminCreateUserOnly: true },
      EmailConfiguration: Match.objectLike({ EmailSendingAccount: 'DEVELOPER' }),
    });
  });

  it('survives a stack deletion in prod', () => {
    template.hasResource('AWS::Cognito::UserPool', { DeletionPolicy: 'Retain' });
  });

  it('gives the script a public client with password sign-in only', () => {
    template.hasResourceProperties('AWS::Cognito::UserPoolClient', {
      GenerateSecret: false,
      ExplicitAuthFlows: Match.arrayWith(['ALLOW_USER_PASSWORD_AUTH']),
    });
  });
});