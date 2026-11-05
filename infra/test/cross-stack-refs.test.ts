/**
 * Guards the deliberate cross-stack reference setting in cdk.json.
 *
 * Regenerating the recommended feature flags on a CDK upgrade would restore 'weak', where
 * a stack can keep a stale value from another stack while its deploy reports success.
 */
import * as fs from 'fs';
import * as path from 'path';

const CDK_JSON = path.join(__dirname, '..', 'cdk.json');

describe('cdk.json', () => {
  it('keeps strong cross-stack references', () => {
    const config = JSON.parse(fs.readFileSync(CDK_JSON, 'utf8'));

    expect(config.context['@aws-cdk/core:defaultCrossStackReferences']).toBe('strong');
  });
});