/**
 * Environment settings for QR Manager.
 *
 * The only place that knows how sandbox and prod differ. Stacks are generic and
 * receive these values through their props, so one set of stack code builds both
 * environments. See docs/DESIGN.md, section 5.
 */
import * as logs from 'aws-cdk-lib/aws-logs';

/** Prefixes every stack and named resource, and tags everything for cost tracking. */
export const APP_NAME = 'qr-manager';

/** Where everything deploys. A CDK "env" is the account and region pair for a stack. */
export const ACCOUNT = '381492047863';

/**
 * Everything is in us-east-1, unlike the us-west-2 sibling apps. CloudFront needs its
 * certificate here, and log delivery must be created here, so one region avoids
 * cross-region references entirely.
 */
export const REGION = 'us-east-1';

/** The existing Route 53 zone. CDK references it and must never create or delete it. */
export const HOSTED_ZONE = {
  hostedZoneId: 'Z08490251WV9146J97IRG',
  zoneName: '360balancedliving.com',
} as const;

/** What differs between environments. */
export interface EnvConfig {
  /** Used in stack and resource names, for example qr-manager-sandbox-Edge. */
  readonly envName: 'sandbox' | 'prod';

  /** Public hostname for redirects. Any path on it is treated as a code. */
  readonly domainName: string;

  /**
   * True keeps data when a stack is deleted, and adds deletion protection.
   * Prod holds records behind printed codes, which cannot be recreated.
   * Sandbox stays disposable so it can be torn down and rebuilt freely.
   */
  readonly retainData: boolean;

  /** Where alarms email. Omitted means the alarm exists but sends nothing. */
  readonly alarmEmail?: string;

  /** How long CloudWatch keeps function and Lambda logs. */
  readonly logRetention: logs.RetentionDays;
}

export const SANDBOX: EnvConfig = {
  envName: 'sandbox',
  domainName: 'qr-sandbox.360balancedliving.com',
  retainData: false,
  logRetention: logs.RetentionDays.ONE_MONTH,
};

export const PROD: EnvConfig = {
  envName: 'prod',
  domainName: 'qr.360balancedliving.com',
  retainData: true,
  alarmEmail: 'saastabp@gmail.com',
  logRetention: logs.RetentionDays.THREE_MONTHS,
};

/** Build an explicit name such as qr-manager-sandbox-Edge. */
export function resourceName(env: EnvConfig, suffix: string): string {
  return `${APP_NAME}-${env.envName}-${suffix}`;
}