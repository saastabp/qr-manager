#!/usr/bin/env node
/**
 * CDK app entry point: creates one stage per environment.
 *
 * Runs on every cdk command. Environment differences live in lib/config.ts; this file
 * only assembles them. The stage ids here are what deploy commands use, for example:
 *
 *   npx cdk deploy 'sandbox/*' --profile brian-admin
 */
import { App } from 'aws-cdk-lib';
import { PROD, REGION, SANDBOX } from '../lib/config';
import { QrManagerStage } from '../lib/stage';

// A CDK "env" is the account and region pair for a stack. The cdk command sets this
// variable from --profile, which keeps the account ID out of this public repository.
const account = process.env.CDK_DEFAULT_ACCOUNT;
if (!account) {
  throw new Error('No AWS account found. Run cdk with --profile brian-admin.');
}

const app = new App();
const env = { account, region: REGION };

new QrManagerStage(app, 'sandbox', { env, config: SANDBOX });
new QrManagerStage(app, 'prod', { env, config: PROD });

app.synth();