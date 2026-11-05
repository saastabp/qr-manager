// CloudFront Function: the code that runs on every scan of a dynamic code.
// Viewer-request event, JS runtime 2.0. It answers the request itself, so the
// distribution's origin is never contacted. See docs/DESIGN.md, section 5.
//
// Rule: this function must always return a 302. A printed code must never show
// an error page, so every failure ends in a redirect to the home page.

import cf from 'cloudfront';

// CDK associates one KeyValueStore with this function, so no ID is needed.
const kvs = cf.kvs();

const HOME_PAGE = 'https://360balancedliving.com/';

// Must match the code format in the backend models.
const CODE_FORMAT = /^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$/;

// Longest path text written to the logs; anything longer is scanner noise.
const MAX_LOGGED_LENGTH = 64;

// Works out where a request should go. The outcome values are:
//   hit      - found in KVS
//   fallback - well-formed code that did not resolve (the alarm watches this)
//   invalid  - not a possible code, so KVS is not read
//   home     - bare / with no code
async function resolve(uri) {
  const code = uri.replace(/^\/+|\/+$/g, '').toLowerCase();

  if (code === '') {
    return { code: '-', location: HOME_PAGE, outcome: 'home', error: '' };
  }
  if (!CODE_FORMAT.test(code)) {
    return { code: code, location: HOME_PAGE, outcome: 'invalid', error: '' };
  }

  // get() throws for a missing key as well as for a read failure.
  try {
    const location = await kvs.get(code);
    return { code: code, location: location, outcome: 'hit', error: '' };
  } catch (err) {
    return { code: code, location: HOME_PAGE, outcome: 'fallback', error: String(err) };
  }
}

async function handler(event) {
  let result = { code: '-', location: HOME_PAGE, outcome: 'fallback', error: '' };

  try {
    result = await resolve(event.request.uri);
  } catch (err) {
    result.error = String(err);
  }

  // Logging must never stop a redirect.
  try {
    const code = result.code.substring(0, MAX_LOGGED_LENGTH);
    // Goes to the access log, in the viewer-request-log-data field.
    cf.logCustomData(code + ':' + result.outcome);
    // Goes to CloudWatch. Date is frozen in this runtime, so duration cannot be
    // measured here; the access log's time-taken field covers it instead.
    console.log(JSON.stringify({
      request_id: event.context.requestId,
      code: code,
      outcome: result.outcome,
      error: result.error,
    }));
  } catch (err) {
    // Deliberately empty: the redirect below must still happen.
  }

  return {
    statusCode: 302,
    statusDescription: 'Found',
    headers: {
      location: { value: result.location },
      'cache-control': { value: 'no-store' },
    },
  };
}