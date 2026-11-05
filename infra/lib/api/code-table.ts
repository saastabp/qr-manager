/**
 * Code table: the source of truth for every code.
 *
 * Prod holds the records behind printed codes, which cannot be recreated, so it has
 * deletion protection, point-in-time recovery, and survives a stack deletion. See
 * docs/DESIGN.md, section 4.
 */
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Construct } from 'constructs';
import { EnvConfig, resourceName } from '../config';
import { removalPolicyFor } from '../defaults';

/** Inputs to this construct. */
export interface CodeTableProps {
  readonly config: EnvConfig;
}

export class CodeTable extends Construct {
  readonly table: dynamodb.Table;

  constructor(scope: Construct, id: string, props: CodeTableProps) {
    super(scope, id);
    const { config } = props;

    this.table = new dynamodb.Table(this, 'Table', {
      tableName: resourceName(config, 'codes'),
      partitionKey: { name: 'code', type: dynamodb.AttributeType.STRING },
      // Pay per request: costs almost nothing at this volume.
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      deletionProtection: config.retainData,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: config.retainData,
      },
      removalPolicy: removalPolicyFor(config),
    });

    // "My codes": a user's codes, newest first. A backend test keeps this name equal to
    // OWNER_INDEX in the adapter.
    this.table.addGlobalSecondaryIndex({
      indexName: 'by-owner',
      partitionKey: { name: 'owner_sub', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'created_at', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });
  }
}