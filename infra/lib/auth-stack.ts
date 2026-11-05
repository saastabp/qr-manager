/**
 * Auth stack: who can sign in.
 *
 * An invite-only user pool and the client the qr-admin script signs in with. The Phase II
 * web client and Managed Login pages are added here later. See docs/DESIGN.md, section 9.
 */
import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { Construct } from 'constructs';
import { AUTH_EMAIL, EnvConfig, REGION, resourceName } from './config';

/** Inputs to this stack. */
export interface AuthStackProps extends StackProps {
  readonly config: EnvConfig;
}

export class AuthStack extends Stack {
  // Public because the Api stack checks tokens against these.
  readonly userPool: cognito.UserPool;
  readonly cliClient: cognito.UserPoolClient;

  constructor(scope: Construct, id: string, props: AuthStackProps) {
    super(scope, id, props);
    const { config } = props;

    // --- User pool ----------------------------------------------------------------
    this.userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: resourceName(config, 'users'),
      // Invite-only: nobody can create their own account.
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      autoVerify: { email: true },
      standardAttributes: { email: { required: true, mutable: true } },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      // Optional authenticator-app MFA. The script does not handle the MFA challenge, so
      // an account using it would need the Phase II sign-in pages.
      mfa: cognito.Mfa.OPTIONAL,
      mfaSecondFactor: { sms: false, otp: true },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      // Send through the verified domain. Cognito's default mailer often lands in spam,
      // and a user who misses an invitation has no other way in.
      email: cognito.UserPoolEmail.withSES({
        fromEmail: AUTH_EMAIL.fromEmail,
        fromName: AUTH_EMAIL.fromName,
        sesRegion: REGION,
        sesVerifiedDomain: AUTH_EMAIL.sesVerifiedDomain,
      }),
      deletionProtection: config.retainData,
      removalPolicy: config.retainData ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
    });

    // --- Script client ------------------------------------------------------------
    // The qr-admin script signs in directly with a password, so it needs no OAuth or
    // hosted pages. A public client: a script cannot keep a secret.
    this.cliClient = this.userPool.addClient('CliClient', {
      userPoolClientName: resourceName(config, 'cli'),
      generateSecret: false,
      disableOAuth: true,
      authFlows: { userPassword: true },
      // Sign-in errors do not reveal whether an account exists.
      preventUserExistenceErrors: true,
      enableTokenRevocation: true,
      idTokenValidity: Duration.hours(1),
      accessTokenValidity: Duration.hours(1),
      refreshTokenValidity: Duration.days(30),
    });

    // --- Outputs ------------------------------------------------------------------
    new CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new CfnOutput(this, 'CliClientId', { value: this.cliClient.userPoolClientId });
  }
}