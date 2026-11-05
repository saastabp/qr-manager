/**
 * Tests for the Analytics stack's generated CloudFormation.
 *
 * Guards the column mappings against the real log format, partition projection, the
 * per-environment names, and the per-query scan limit.
 */
import { App, Stack } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { EnvConfig, PROD, SANDBOX } from '../lib/config';
import { QrManagerStage } from '../lib/stage';

const ENV = { account: '111111111111', region: 'us-east-1' };

/** Field names from a real delivered log file. */
const REAL_LOG_FIELDS = [
  'timestamp(ms)',
  'x-edge-request-id',
  'c-ip',
  'cs-uri-stem',
  'sc-status',
  'cs(User-Agent)',
  'c-country',
  'x-edge-result-type',
  'time-taken',
  'viewer-request-log-data',
];

/** Build the stack as the real app does, skipping the slow Lambda bundling. */
function templateFor(config: EnvConfig): Template {
  const app = new App({ context: { 'aws:cdk:bundling-stacks': [] } });
  const stage = new QrManagerStage(app, config.envName, { env: ENV, config });
  return Template.fromStack(stage.node.findChild('Analytics') as Stack);
}

/** Return the table's definition from the template. */
function tableInput(template: Template) {
  const [table] = Object.values(template.findResources('AWS::Glue::Table'));
  return table.Properties.TableInput;
}

describe('scans table', () => {
  const table = tableInput(templateFor(SANDBOX));

  it('reads every field in a real log line', () => {
    const { Parameters: mappings } = table.StorageDescriptor.SerdeInfo;

    expect(Object.values(mappings).sort()).toEqual([...REAL_LOG_FIELDS].sort());
  });

  it('has a mapping for every column', () => {
    const columns = table.StorageDescriptor.Columns.map(
      (column: { Name: string }) => `mapping.${column.Name}`,
    );

    expect(columns.sort()).toEqual(
      Object.keys(table.StorageDescriptor.SerdeInfo.Parameters).sort(),
    );
  });

  it('finds log folders by projection, matching the delivered layout', () => {
    expect(table.Parameters['projection.enabled']).toBe('true');
    expect(table.Parameters['projection.month.digits']).toBe('2');
    expect(table.Parameters['projection.day.digits']).toBe('2');
    expect(JSON.stringify(table.Parameters['storage.location.template'])).toContain(
      '/qr-logs/year=${year}/month=${month}/day=${day}',
    );
  });
});

describe('workgroup', () => {
  it('limits how much one query can read, and cannot be overridden', () => {
    templateFor(SANDBOX).hasResourceProperties('AWS::Athena::WorkGroup', {
      WorkGroupConfiguration: Match.objectLike({
        EnforceWorkGroupConfiguration: true,
        BytesScannedCutoffPerQuery: 100 * 1024 * 1024,
      }),
    });
  });
});

describe('names', () => {
  it.each([
    [SANDBOX, 'qr_analytics_sandbox', 'qr_manager_sandbox'],
    [PROD, 'qr_analytics_prod', 'qr_manager_prod'],
  ])('differ by environment, as they share an account', (config, workGroup, database) => {
    const template = templateFor(config);

    template.hasResourceProperties('AWS::Athena::WorkGroup', { Name: workGroup });
    template.hasResourceProperties('AWS::Glue::Database', {
      DatabaseInput: { Name: database },
    });
  });
});