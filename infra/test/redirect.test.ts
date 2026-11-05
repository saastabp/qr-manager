/**
 * Tests for the redirect CloudFront Function.
 *
 * Runs the shipped file in a vm sandbox, so a bad edit to redirect.js cannot pass by
 * being absent from the test. This proves the logic, not the CloudFront runtime itself;
 * `aws cloudfront test-function` covers that after a deploy.
 */
import * as fs from 'fs';
import * as path from 'path';
import * as vm from 'vm';

const HOME_PAGE = 'https://360balancedliving.com/';
const FINAL_URL = 'https://360balancedliving.com/bldc?utm_source=qr';

/** A KVS value, as the backend publishes it. */
const ENTRY = JSON.stringify({ url: FINAL_URL, campaign: 'bldc' });

const SOURCE_PATH = path.join(__dirname, '..', 'lib', 'cloudfront-functions', 'redirect.js');
const SOURCE = fs.readFileSync(SOURCE_PATH, 'utf8');

interface Redirect {
  statusCode: number;
  headers: Record<string, { value: string }>;
}

interface Options {
  readonly kvsFails?: boolean;
  readonly loggingFails?: boolean;
}

/** Load the shipped function with a fake `cf` module and capture what it logs. */
function load(store: Record<string, string>, options: Options = {}) {
  const reads: string[] = [];
  const customData: string[] = [];
  const logs: string[] = [];

  const cf = {
    kvs: () => ({
      get: async (key: string): Promise<string> => {
        reads.push(key);
        if (options.kvsFails) {
          throw new Error('KVS unavailable');
        }
        if (!(key in store)) {
          throw new Error('KeyNotFound');
        }
        return store[key];
      },
    }),
    logCustomData: (value: string): void => {
      if (options.loggingFails) {
        throw new Error('logging failed');
      }
      customData.push(value);
    },
  };

  const code = `${SOURCE.replace(/^import cf from 'cloudfront';$/m, '')}\n;handler`;
  // The filename ties this code to redirect.js, so debugger breakpoints work there.
  const handler = vm.runInNewContext(
    code,
    {
      cf,
      console: { log: (message: string) => logs.push(message) },
    },
    { filename: SOURCE_PATH },
  ) as (event: unknown) => Promise<Redirect>;

  return { handler, reads, customData, logs };
}

/** Build the event CloudFront passes to the function. */
function eventFor(uri: string) {
  return { context: { requestId: 'request-1' }, request: { uri } };
}

describe('redirect function', () => {
  it('avoids await inside call arguments, which the CloudFront runtime rejects', () => {
    expect(SOURCE).not.toMatch(/\(\s*await\b/);
  });

  it('sends a known code to its final URL', async () => {
    const { handler, customData } = load({ 'bldc-flyer': ENTRY });

    const response = await handler(eventFor('/bldc-flyer'));

    expect(response.statusCode).toBe(302);
    expect(response.headers.location.value).toBe(FINAL_URL);
    expect(customData).toEqual(['bldc-flyer:bldc:hit']);
  });

  it('logs an empty campaign when a code has none', async () => {
    const { handler, customData } = load({
      'one-off': JSON.stringify({ url: FINAL_URL, campaign: '' }),
    });

    await handler(eventFor('/one-off'));

    expect(customData).toEqual(['one-off::hit']);
  });

  it.each([
    ['cannot be parsed', 'https://example.com/plain-url'],
    ['has no url', JSON.stringify({ campaign: 'bldc' })],
  ])('sends a code home when its value %s, with an error the alarm sees', async (_name, value) => {
    const { handler, customData, logs } = load({ 'bldc-flyer': value });

    const response = await handler(eventFor('/bldc-flyer'));

    expect(response.statusCode).toBe(302);
    expect(response.headers.location.value).toBe(HOME_PAGE);
    expect(customData).toEqual(['bldc-flyer::fallback']);
    expect(JSON.parse(logs[0]).error).not.toBe('');
    expect(JSON.parse(logs[0]).error).not.toContain('KeyNotFound');
  });

  it.each(['/BLDC-Flyer', '/bldc-flyer/', '//bldc-flyer//'])('normalises %s', async (uri) => {
    const { handler } = load({ 'bldc-flyer': ENTRY });

    const response = await handler(eventFor(uri));

    expect(response.headers.location.value).toBe(FINAL_URL);
  });

  it('sends bare / home without reading KVS', async () => {
    const { handler, reads, customData } = load({});

    const response = await handler(eventFor('/'));

    expect(response.headers.location.value).toBe(HOME_PAGE);
    expect(reads).toEqual([]);
    expect(customData).toEqual(['-::home']);
  });

  it.each(['/favicon.ico', '/a/b', '/-bad', '/bad-', `/${'a'.repeat(65)}`])(
    'sends the invalid path %s home without reading KVS',
    async (uri) => {
      const { handler, reads, customData } = load({});

      const response = await handler(eventFor(uri));

      expect(response.statusCode).toBe(302);
      expect(response.headers.location.value).toBe(HOME_PAGE);
      expect(reads).toEqual([]);
      expect(customData[0]).toMatch(/:invalid$/);
    },
  );

  it('sends an unknown code home as a fallback', async () => {
    const { handler, customData } = load({});

    const response = await handler(eventFor('/no-such-code'));

    expect(response.headers.location.value).toBe(HOME_PAGE);
    expect(customData).toEqual(['no-such-code::fallback']);
  });

  it('still redirects when KVS fails, and logs the error', async () => {
    const { handler, logs } = load({ 'bldc-flyer': ENTRY }, { kvsFails: true });

    const response = await handler(eventFor('/bldc-flyer'));

    expect(response.statusCode).toBe(302);
    expect(response.headers.location.value).toBe(HOME_PAGE);
    expect(JSON.parse(logs[0]).error).toContain('KVS unavailable');
  });

  it('still redirects when the event is malformed', async () => {
    const { handler, customData } = load({});

    const response = await handler({ context: { requestId: 'request-1' } });

    expect(response.statusCode).toBe(302);
    expect(response.headers.location.value).toBe(HOME_PAGE);
    expect(customData).toEqual(['-::fallback']);
  });

  it('still redirects when logging fails', async () => {
    const { handler } = load({ 'bldc-flyer': ENTRY }, { loggingFails: true });

    const response = await handler(eventFor('/bldc-flyer'));

    expect(response.headers.location.value).toBe(FINAL_URL);
  });

  it('never lets a redirect be cached', async () => {
    const { handler } = load({ 'bldc-flyer': ENTRY });

    const response = await handler(eventFor('/bldc-flyer'));

    expect(response.headers['cache-control'].value).toBe('no-store');
  });

  it('logs one pairable line per request', async () => {
    const { handler, logs } = load({ 'bldc-flyer': ENTRY });

    await handler(eventFor('/bldc-flyer'));

    expect(logs).toHaveLength(1);
    expect(JSON.parse(logs[0])).toEqual({
      request_id: 'request-1',
      code: 'bldc-flyer',
      campaign: 'bldc',
      outcome: 'hit',
      error: '',
    });
  });

  it('truncates long paths in the logs', async () => {
    const { handler, customData } = load({});

    await handler(eventFor(`/${'a'.repeat(200)}`));

    expect(customData[0]).toBe(`${'a'.repeat(64)}::invalid`);
  });
});