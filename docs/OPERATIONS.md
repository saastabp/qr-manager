# QR Manager: operations

Commands use `sandbox`. For prod, replace it with `prod`. All AWS commands use the `brian-admin` profile
in us-east-1. Sign in first with `aws sso login --profile brian-admin`.

## Deploying

Run from `infra/`. Every change goes to sandbox first.

```bash
npx cdk diff 'sandbox/*' --profile brian-admin
npx cdk deploy 'sandbox/*' --profile brian-admin --outputs-file outputs-sandbox.json
```

Always keep `--outputs-file`, because `qr-admin` reads that file. Each environment has its own. The file
is overwritten on each deploy, so a single-stack deploy must use a different file name.

Every `cdk` command needs `--profile`, because the account comes from it. This repository is public, so
account and zone IDs stay out of it. CDK caches its Route 53 lookup in `infra/cdk.context.json`, which git
ignores. Never commit that file.

Read the diff carefully for the Edge stack. Printed codes depend on it, and redirect function changes go
live as soon as the deploy finishes.

### After every Edge deploy

Run the function in the real CloudFront runtime. Jest runs it in Node, which allows code that CloudFront
rejects, and a function that cannot load breaks every scan.

```bash
FUNCTION=qr-manager-sandbox-redirect
ETAG=$(aws cloudfront describe-function --profile brian-admin --name "$FUNCTION" --stage LIVE \
  --query ETag --output text)
echo '{"version":"1.0","context":{"eventType":"viewer-request","requestId":"check"},"viewer":{"ip":"1.2.3.4"},"request":{"method":"GET","uri":"/","querystring":{},"headers":{},"cookies":{}}}' \
  > /tmp/qr-event.json
aws cloudfront test-function --profile brian-admin --name "$FUNCTION" --if-match "$ETAG" --stage LIVE \
  --event-object fileb:///tmp/qr-event.json \
  --query 'TestResult.{error:FunctionErrorMessage,compute:ComputeUtilization}'
```

`error` must be empty. Then confirm a real redirect with `curl -sI https://<domain>/` and look for a 302.

## First prod deploy

1. Run the checks in the [README](../README.md), and confirm the change works in sandbox.
2. Deploy with `npx cdk deploy 'prod/*' --profile brian-admin --outputs-file outputs-prod.json`.
3. Click the confirmation link in the SNS email. Alarm emails do not arrive until this is done.
4. Add a prod user, as below. Sandbox users do not carry over.
5. Create a test code, then scan its printed PNG with a phone before printing anything in volume.

## Adding a user

Users are created by hand until Phase II. Run these from the project root, not `infra/`, because the paths
are relative to it. The password is set directly because `qr-admin login` does not handle the forced
password change that an invitation requires. Passwords need at least 8 characters, with upper and lower
case letters, a digit, and a symbol.

Do not turn on MFA for a user of `qr-admin`. The pool allows it, but `login` does not handle the challenge.

```bash
POOL_ID=$(jq -r '."qr-manager-sandbox-Auth".UserPoolId' infra/outputs-sandbox.json)
USER_EMAIL=person@example.com

aws cognito-idp admin-create-user --profile brian-admin --region us-east-1 \
  --user-pool-id "$POOL_ID" --username "$USER_EMAIL" --message-action SUPPRESS \
  --user-attributes Name=email,Value="$USER_EMAIL" Name=email_verified,Value=true

read -rsp 'Password: ' USER_PASSWORD; echo
aws cognito-idp admin-set-user-password --profile brian-admin --region us-east-1 \
  --user-pool-id "$POOL_ID" --username "$USER_EMAIL" --password "$USER_PASSWORD" --permanent
unset USER_PASSWORD
```

## Querying scans

Open Athena in us-east-1 and select the workgroup for the environment.

| Environment | Workgroup | Database |
|---|---|---|
| Sandbox | `qr_analytics_sandbox` | `qr_manager_sandbox` |
| Prod | `qr_analytics_prod` | `qr_manager_prod` |

The workgroup does not choose the data. The database in `FROM` does, so a prod workgroup querying
`qr_manager_sandbox` returns sandbox rows.

Each database has two objects. Use `scans` for reporting, and `scan_logs` only to look at noise or
troubleshoot.

| Object | Holds |
|---|---|
| `scans` | Real scans only, with bots and non-scan requests removed |
| `scan_logs` | Every request as logged, including bots |

| `scans` column | Notes |
|---|---|
| `code` | The code name |
| `campaign` | The code's campaign at the time of the scan; NULL when it has none |
| `outcome` | `hit`, or NULL for a scan from an embedded POP, which can omit it |
| `scanned_at` | Hawaii time |
| `client_ip`, `country`, `user_agent` | `user_agent` is URL-encoded, so spaces appear as `%20` |
| `year`, `month`, `day` | Strings such as `'11'` and `'05'`, in UTC. Always filter on them |

Static codes never appear, because they do not touch this infrastructure.

Counts are approximate. A bot that imitates a browser still gets through, but it would have to request a
real code name to count. `approx_visitors` counts IP addresses, and one phone can change address, or
several can share one. Partitions are in UTC, so a scan on the evening of the last day of a month in Hawaii
lands in the next month's folder.

**Most recent scans**

```sql
SELECT scanned_at, code, campaign, country, user_agent
FROM qr_manager_sandbox.scans
WHERE year = '2026' AND month = '11'
ORDER BY scanned_at DESC
LIMIT 50;
```

**Scans per code**

```sql
SELECT code, count(*) AS scans, approx_distinct(client_ip) AS approx_visitors
FROM qr_manager_sandbox.scans
WHERE year = '2026' AND month = '11'
GROUP BY 1
ORDER BY scans DESC;
```

**Scans per campaign**

```sql
SELECT coalesce(campaign, '(none)') AS campaign,
       count(*) AS scans,
       approx_distinct(client_ip) AS approx_visitors
FROM qr_manager_sandbox.scans
WHERE year = '2026' AND month = '11'
GROUP BY 1
ORDER BY scans DESC;
```

**Codes in one campaign**

```sql
SELECT code, count(*) AS scans
FROM qr_manager_sandbox.scans
WHERE year = '2026' AND month = '11'
  AND campaign = 'spring-2027'
GROUP BY 1
ORDER BY scans DESC;
```

**Scans per code per day**

```sql
SELECT date(scanned_at) AS scan_date, code,
       count(*) AS scans,
       approx_distinct(client_ip) AS approx_visitors
FROM qr_manager_sandbox.scans
WHERE year = '2026' AND month = '11'
GROUP BY 1, 2
ORDER BY 1 DESC, scans DESC;
```

**iPhone and Android**

```sql
SELECT CASE
         WHEN user_agent LIKE '%iPhone%' OR user_agent LIKE '%iPad%' THEN 'iOS'
         WHEN user_agent LIKE '%Android%' THEN 'Android'
         ELSE 'Other'
       END AS device,
       count(*) AS scans
FROM qr_manager_sandbox.scans
WHERE year = '2026' AND month = '11'
GROUP BY 1
ORDER BY scans DESC;
```

**Scans by country for one code**

```sql
SELECT country, count(*) AS scans
FROM qr_manager_sandbox.scans
WHERE year = '2026' AND month = '11'
  AND code = 'spring-flyer'
GROUP BY 1
ORDER BY scans DESC;
```

**Busiest hours of the day**

```sql
SELECT hour(scanned_at) AS hour_of_day, count(*) AS scans
FROM qr_manager_sandbox.scans
WHERE year = '2026' AND month = '11'
GROUP BY 1
ORDER BY 1;
```

### Looking at raw logs

`scan_logs` has every request. `outcome_data` is `<code>:<campaign>:<outcome>`, or `-` when the function
recorded nothing. `result_type` is `FunctionGeneratedResponse` when the function ran, `Redirect` for the
HTTP to HTTPS 301, and `Error` for a refused request. `timestamp_ms` is a string, and every value is a
string, so cast before doing arithmetic.

**Traffic by outcome**, to see how much is noise and whether function responses are missing outcomes:

```sql
SELECT regexp_extract(outcome_data, '[^:]+$') AS outcome,
       count(*) AS requests
FROM qr_manager_sandbox.scan_logs
WHERE year = '2026' AND month = '11'
  AND result_type = 'FunctionGeneratedResponse'
GROUP BY 1
ORDER BY 2 DESC;
```

**Code-like paths that did not match.** This can show a mistyped or never-created code, though most of it
will be bots.

```sql
SELECT uri_stem, count(*) AS requests
FROM qr_manager_sandbox.scan_logs
WHERE year = '2026' AND month = '11'
  AND outcome_data LIKE '%:fallback'
GROUP BY 1
ORDER BY requests DESC
LIMIT 25;
```

### Query limits

A query fails if it would read more than 100 MB. That limit is a guard against mistakes, so narrow the
partitions rather than raising it. Results are stored under `athena-results/` in the log bucket, and they
expire with the logs after 400 days. They can contain IP addresses, so do not copy them elsewhere.

## Alarms

All three alarms use the `qr-manager-<env>-alerts` topic. Only prod emails.

| Alarm | Meaning | First step |
|---|---|---|
| `qr-manager-<env>-function-errors` | The redirect function could not run, so **scans are getting a 503** | Run the [check after an Edge deploy](#after-every-edge-deploy) to see the error. If a deploy caused it, fix and redeploy straight away |
| `qr-manager-<env>-kvs-failures` | The redirect function could not read KVS or parse a value, so scans are going to the home page | Read `/aws/cloudfront/function/qr-manager-<env>-redirect`, then check the AWS Health Dashboard for CloudFront |
| `qr-manager-<env>-step-failures` | A publish or render failed, so a code is `pending` | Read `/aws/lambda/qr-manager-<env>-api`, then run `resync` |

`function-errors` uses CloudFront's own metrics, because a function that cannot load writes no logs. Those
metrics arrive a few minutes late.

An ordinary miss, such as a bot requesting a path that looks like a code, logs `KeyNotFound` and does not
alarm. A real KVS read failure has not been observed yet, so its log text is unconfirmed.

## Fixing a `pending` code

A code stays `pending` until its redirect is published and its PNG exists. Nothing is lost, and repeating a
step is safe.

```bash
cli/qr-admin --env sandbox resync          # finish every pending code
cli/qr-admin --env sandbox resync --all    # republish every code you own
cli/qr-admin --env sandbox update my-code  # no options: finish or republish one code
```

A code changed while `pending` keeps sending scans to its old destination until this succeeds. This matters
most when retracting a URL, so check `list` afterwards.

`resync` covers only the signed-in user's codes.

## Tracing a request

- **API:** every error shows a correlation ID. Search for it in `/aws/lambda/qr-manager-<env>-api` and
  `/aws/lambda/qr-manager-<env>-render`. Every error response logs its body and stack trace at `ERROR`,
  including an ordinary 400 or 404, so `ERROR` alone does not mean something is broken.
- **Scan:** the `request_id` column in `scan_logs` matches the `request_id` field in the JSON of the
  function's log line. Use that field, not the ID CloudFront puts before the JSON, because the two can
  differ. That prefix also means the line is not valid JSON, so it needs text matching in CloudWatch.
