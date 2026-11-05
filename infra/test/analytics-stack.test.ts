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

/** Return a table or view's definition from the template, by name. */
function tableInput(template: Template, name: string) {
  const tables = Object.values(template.findResources('AWS::Glue::Table'));
  const match = tables.find((table) => table.Properties.TableInput.Name === name);
  return match?.Properties.TableInput;
}

/** Decode the definition Athena stores inside a view. */
function viewDefinition(view: { ViewOriginalText: string }) {
  const encoded = view.ViewOriginalText.replace('/* Presto View: ', '').replace(' */', '');
  return JSON.parse(Buffer.from(encoded, 'base64').toString('utf8'));
}

describe('scan_logs table', () => {
  const table = tableInput(templateFor(SANDBOX), 'scan_logs');

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

describe('scans view', () => {
  const view = tableInput(templateFor(SANDBOX), 'scans');
  const definition = viewDefinition(view);

  it('is a view over scan_logs in the same database', () => {
    expect(view.TableType).toBe('VIRTUAL_VIEW');
    expect(definition.schema).toBe('qr_manager_sandbox');
    expect(definition.originalSql).toContain('FROM "qr_manager_sandbox"."scan_logs"');
  });

  it('offers code, campaign, and outcome as named columns', () => {
    const names = definition.columns.map((column: { name: string }) => column.name);

    expect(names).toEqual(expect.arrayContaining(['code', 'campaign', 'outcome', 'scanned_at']));
  });

  it('declares the same columns to Glue and Athena', () => {
    const glueNames = view.StorageDescriptor.Columns.map((c: { Name: string }) => c.Name);
    const athenaNames = definition.columns.map((c: { name: string }) => c.name);

    expect(glueNames).toEqual(athenaNames);
  });

  it('shows scan times in local time, by formatting before the cast', () => {
    expect(definition.originalSql).toMatch(
      /format_datetime\([\s\S]*AT TIME ZONE 'Pacific\/Honolulu', 'yyyy-MM-dd HH:mm:ss'\)/,
    );
  });

  it('keeps only real scans', () => {
    expect(definition.originalSql).toContain("result_type = 'FunctionGeneratedResponse'");
    expect(definition.originalSql).toContain("outcome_data LIKE '%:hit'");
    expect(definition.originalSql).toContain('NOT regexp_like(lower(user_agent)');
  });

  describe('bot filter', () => {
    const pattern = definition.originalSql.match(/regexp_like\(lower\(user_agent\), '([^']+)'\)/);
    const isBot = (userAgent: string) => new RegExp(pattern[1]).test(userAgent.toLowerCase());

    it.each([
      'facebookexternalhit/1.1%20(+http://www.facebook.com/externalhit_uatext.php)',
      'WhatsApp/2.23.20.0',
      'Mozilla/5.0%20(compatible;%20Googlebot/2.1)',
    ])('removes the fetcher %s', (userAgent) => {
      expect(isBot(userAgent)).toBe(true);
    });

    it.each([
      'Mozilla/5.0%20(iPhone;%20CPU%20iPhone%20OS%2026_6_1)%20CriOS/156.0.8078.54%20Safari/604.1',
      'Mozilla/5.0%20(iPhone)%20Mobile/23G83%20%5BFBAN/FBIOS;FBAV/585.0.0.28.107%5D',
    ])('keeps the real browser %s', (userAgent) => {
      expect(isBot(userAgent)).toBe(false);
    });
  });

  it('counts a row with no outcome only if its path looks like a code', () => {
    expect(definition.originalSql).toMatch(/outcome_data = '-' AND regexp_like\(code, '\^/);
  });

  it('normalises the code as the redirect function does', () => {
    expect(definition.originalSql).toContain(
      "lower(regexp_replace(uri_stem, '^/+|/+$', '')) AS code",
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