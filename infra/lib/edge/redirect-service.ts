/**
 * Redirect service: everything a scan passes through.
 *
 * Creates the certificate, the code store, the redirect function, the distribution, and
 * the DNS records. See docs/DESIGN.md, section 5.
 */
import * as path from 'path';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as targets from 'aws-cdk-lib/aws-route53-targets';
import { Construct } from 'constructs';
import { EnvConfig, resourceName, ZONE_NAME } from '../config';
import { logGroupProps, removalPolicyFor } from '../defaults';

/** Inputs to this construct. */
export interface RedirectServiceProps {
  readonly config: EnvConfig;
}

export class RedirectService extends Construct {
  // Public so the parent stack can pass them to other constructs and stacks.
  readonly keyValueStore: cloudfront.KeyValueStore;
  readonly distribution: cloudfront.Distribution;
  readonly functionName: string;
  readonly functionLogs: logs.LogGroup;

  constructor(scope: Construct, id: string, props: RedirectServiceProps) {
    super(scope, id);
    const { config } = props;

    // --- Domain and certificate ---------------------------------------------------
    // Looks up the existing zone by name. This reads it; it does not create or own it.
    // CDK saves the answer in cdk.context.json, which git ignores because it holds the
    // account and zone IDs. A profile for another account finds no zone and stops here.
    const zone = route53.HostedZone.fromLookup(this, 'Zone', { domainName: ZONE_NAME });

    // HTTPS certificate. CDK adds the DNS validation record to the zone automatically.
    const certificate = new acm.Certificate(this, 'Certificate', {
      domainName: config.domainName,
      validation: acm.CertificateValidation.fromDns(zone),
    });

    // --- Redirect data and function -----------------------------------------------
    // Holds code -> final URL. The API writes it; the function reads it.
    this.keyValueStore = new cloudfront.KeyValueStore(this, 'CodeStore', {
      keyValueStoreName: resourceName(config, 'codes'),
      comment: 'Maps QR codes to final URLs',
    });
    this.keyValueStore.applyRemovalPolicy(removalPolicyFor(config));

    this.functionName = resourceName(config, 'redirect');
    const redirectFunction = new cloudfront.Function(this, 'RedirectFunction', {
      functionName: this.functionName,
      code: cloudfront.FunctionCode.fromFile({
        filePath: path.join(__dirname, '..', 'cloudfront-functions', 'redirect.js'),
      }),
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      keyValueStore: this.keyValueStore,
    });

    // CloudFront writes function logs to this exact name.
    this.functionLogs = new logs.LogGroup(
      this,
      'RedirectLogs',
      logGroupProps(config, `/aws/cloudfront/function/${this.functionName}`),
    );

    // --- Distribution -------------------------------------------------------------
    this.distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: this.functionName,
      domainNames: [config.domainName],
      certificate,
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      // Cheapest tier: North America and Europe edge locations.
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      defaultBehavior: {
        // Required by CloudFront, but never contacted: the function always answers.
        origin: new origins.HttpOrigin(ZONE_NAME),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
        // Never cache, so a destination change takes effect without an invalidation.
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        functionAssociations: [
          {
            function: redirectFunction,
            eventType: cloudfront.FunctionEventType.VIEWER_REQUEST,
          },
        ],
      },
    });
    // Make sure the log group exists before the function starts taking traffic.
    this.distribution.node.addDependency(this.functionLogs);

    // --- DNS ----------------------------------------------------------------------
    // Points the hostname at the distribution, for IPv4 and IPv6.
    const target = route53.RecordTarget.fromAlias(new targets.CloudFrontTarget(this.distribution));
    new route53.ARecord(this, 'AliasA', { zone, recordName: config.domainName, target });
    new route53.AaaaRecord(this, 'AliasAaaa', { zone, recordName: config.domainName, target });
  }
}