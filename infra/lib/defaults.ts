/**
 * Settings shared by resources in several stacks.
 *
 * These return props rather than creating resources, so each construct keeps its own scope
 * and id. CDK builds a resource's CloudFormation ID from those, and moving a deployed
 * resource to a new position would make CloudFormation try to replace it.
 */
import { RemovalPolicy } from 'aws-cdk-lib';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { EnvConfig } from './config';

/** RETAIN leaves a resource in place when its stack is deleted. */
export function removalPolicyFor(config: EnvConfig): RemovalPolicy {
  return config.retainData ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY;
}

/** A bucket nobody can reach from the internet. Add lifecycle rules at the call site. */
export function privateBucketProps(config: EnvConfig): s3.BucketProps {
  return {
    blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
    encryption: s3.BucketEncryption.S3_MANAGED,
    enforceSSL: true,
    removalPolicy: removalPolicyFor(config),
    // Sandbox only: empties the bucket so the stack can be deleted.
    autoDeleteObjects: !config.retainData,
  };
}

/**
 * A log group with the environment's retention. Creating one by name sets retention, which
 * a log group that AWS creates automatically would not have.
 */
export function logGroupProps(config: EnvConfig, logGroupName: string): logs.LogGroupProps {
  return {
    logGroupName,
    retention: config.logRetention,
    removalPolicy: RemovalPolicy.DESTROY,
  };
}