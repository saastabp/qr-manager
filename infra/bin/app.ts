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
import { ACCOUNT, PROD, REGION, SANDBOX } from '../lib/config';
import { QrManagerStage } from '../lib/stage';

const app = new App();
const env = { account: ACCOUNT, region: REGION };

new QrManagerStage(app, 'sandbox', { env, config: SANDBOX });
new QrManagerStage(app, 'prod', { env, config: PROD });

app.synth();