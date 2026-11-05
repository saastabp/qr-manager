/**
 * Render service: creates and stores PNGs.
 *
 * The render Lambda does nothing else. It can write to the images bucket and has no
 * access to DynamoDB. See docs/DESIGN.md, section 5.
 */
import { Duration } from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { EnvConfig, resourceName } from '../config';
import { logGroupProps, privateBucketProps } from '../defaults';
import { LAMBDA_ARCH, PYTHON_RUNTIME, pythonBundle } from '../python-lambda';

/** Inputs to this construct. */
export interface RenderServiceProps {
  readonly config: EnvConfig;
}

export class RenderService extends Construct {
  // Public because the API Lambda reads the bucket and invokes the function.
  readonly bucket: s3.Bucket;
  readonly renderFunction: lambda.Function;

  constructor(scope: Construct, id: string, props: RenderServiceProps) {
    super(scope, id);
    const { config } = props;

    // --- Images bucket ------------------------------------------------------------
    // Private: clients download through short-lived presigned URLs. PNGs can always be
    // regenerated, so this needs no special retention beyond the environment's default.
    this.bucket = new s3.Bucket(this, 'Bucket', privateBucketProps(config));

    // --- Render Lambda ------------------------------------------------------------
    const functionName = resourceName(config, 'render');
    this.renderFunction = new lambda.Function(this, 'Function', {
      functionName,
      runtime: PYTHON_RUNTIME,
      architecture: LAMBDA_ARCH,
      handler: 'render_handler.lambda_handler',
      code: pythonBundle('render'),
      // Sizing from docs/DESIGN.md. The timeout fails a stuck render quickly, and a backend
      // test keeps it below the read timeout in backend/src/bootstrap.py.
      memorySize: 1024,
      timeout: Duration.seconds(10),
      environment: {
        IMAGES_BUCKET: this.bucket.bucketName,
        POWERTOOLS_SERVICE_NAME: functionName,
      },
      logGroup: new logs.LogGroup(
        this,
        'Logs',
        logGroupProps(config, `/aws/lambda/${functionName}`),
      ),
    });

    // Least privilege: write PNGs, and nothing else.
    this.bucket.grantPut(this.renderFunction);
  }
}