# QR Manager: operations

Commands use `sandbox`. For prod, replace it with `prod`. All AWS commands use the `brian-admin` profile
in us-east-1. Sign in first with `aws sso login --profile brian-admin`.

## Deploying

Run from `infra/`. Every change goes to sandbox first.

```bash
npx cdk diff 'sandbox/*' --profile brian-admin
npx cdk deploy 'sandbox/*' --profile brian-admin --outputs-file outputs-sandbox.json
```

Always keep `--outputs-file`, because `qr-admin` reads that file. Each environment has its own.

Read the diff carefully for the Edge stack. Printed codes depend on it, and redirect function changes go
live as soon as the deploy finishes.

## First prod deploy

1. Run the checks in the [README](../README.md), and confirm the change works in sandbox.
2. Deploy with `npx cdk deploy 'prod/*' --profile brian-admin --outputs-file outputs-prod.json`.
3. Click the confirmation link in the SNS email. Alarm emails do not arrive until this is done.
4. Add a prod user, as below. Sandbox users do not carry over.
5. Create a test code, then scan its printed PNG with a phone before printing anything in volume.

## Adding a user

Users are created by hand until Phase II. The password is set directly because `qr-admin login` does not
handle the forced password change that an invitation requires.

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

| Environment | Workgroup | Table |
|---|---|---|
| Sandbox | `qr_analytics_sandbox` | `qr_manager_sandbox.scans` |
| Prod | `qr_analytics_prod` | `qr_manager_prod.scans` |

**Most rows are not scans.** Bots probe every public hostname. In the first sandbox sample, only 8 of 292
function responses were `hit`. Never treat a row count as a scan count.

| Column | Notes |
|---|---|
| `year`, `month`, `day` | Partitions, as strings such as `'11'` and `'05'`, in UTC. Always filter on them |
| `uri_stem` | The path, such as `/spring-flyer` |
| `outcome_data` | `<code>:<outcome>`, or `-` when the function recorded nothing |
| `result_type` | `FunctionGeneratedResponse` means the function ran. `Redirect` is the HTTP to HTTPS 301, and `Error` is a refused request |
| `user_agent` | URL-encoded, so spaces appear as `%20` |
| `timestamp_ms` | Milliseconds as a string |

Every value is a string, so cast before doing arithmetic. Static codes never appear, because they do not
touch this infrastructure.

**What counts as a scan:** a function response that is a `hit`, or has no outcome (`-`), on a path other
than `/`. The `-` case keeps scans from embedded POPs, which can omit custom data. It could admit a little
noise, so treat counts as approximate. `approx_visitors` is approximate too: it counts IP addresses, and
one phone can change address, or several can share one.

**Scans per code**

```sql
SELECT substr(uri_stem, 2) AS code,
       count(*) AS scans,
       approx_distinct(client_ip) AS approx_visitors
FROM qr_manager_sandbox.scans
WHERE year = '2026' AND month = '11'
  AND result_type = 'FunctionGeneratedResponse'
  AND (outcome_data LIKE '%:hit' OR outcome_data = '-')
  AND uri_stem <> '/'
  AND NOT regexp_like(lower(user_agent),
      'bot|crawl|spider|l9scan|checker|-check|curl|wget|python|go-http|headless|preview')
GROUP BY 1
ORDER BY scans DESC;
```

The bot list matches on `user_agent` directly, because none of its words contain a space. It leaves out
the plain word `scan`, because a phone's QR scanner app could include it. A bot that imitates a browser
still gets through, but it would have to request a real code name to count.

**Scans of one code by day and country**

```sql
SELECT year, month, day, country, count(*) AS scans
FROM qr_manager_sandbox.scans
WHERE year = '2026' AND month = '11'
  AND result_type = 'FunctionGeneratedResponse'
  AND uri_stem = '/spring-flyer'
  AND (outcome_data LIKE '%:hit' OR outcome_data = '-')
GROUP BY 1, 2, 3, 4
ORDER BY 1, 2, 3, scans DESC;
```

**Traffic by outcome**, to see how much is noise and whether function responses are missing outcomes:

```sql
SELECT regexp_extract(outcome_data, '[^:]+$') AS outcome,
       count(*) AS requests
FROM qr_manager_sandbox.scans
WHERE year = '2026' AND month = '11'
  AND result_type = 'FunctionGeneratedResponse'
GROUP BY 1
ORDER BY 2 DESC;
```

A query fails if it would read more than 100 MB. That limit is a guard against mistakes, so narrow the
partitions rather than raising it. Results are stored under `athena-results/` in the log bucket, and they
expire with the logs after 400 days. They can contain IP addresses, so do not copy them elsewhere.

## Alarms

Both alarms use the `qr-manager-<env>-alerts` topic. Only prod emails.

| Alarm | Meaning | First step |
|---|---|---|
| `qr-manager-<env>-kvs-failures` | The redirect function could not read KVS, so scans are going to the home page | Read `/aws/cloudfront/function/qr-manager-<env>-redirect`, then check the AWS Health Dashboard for CloudFront |
| `qr-manager-<env>-step-failures` | A publish or render failed, so a code is `pending` | Read `/aws/lambda/qr-manager-<env>-api`, then run `resync` |

An ordinary miss, such as a bot requesting a path that looks like a code, logs `KeyNotFound` and does not
alarm. A real KVS read failure has not been observed yet, so its log text is unconfirmed.

## Fixing a `pending` code

A code stays `pending` until its redirect is published and its PNG exists. Nothing is lost, and repeating a
step is safe.

```bash
cli/qr-admin --env sandbox resync          # finish every pending code
cli/qr-admin --env sandbox resync --all    # republish every code you own
```

A code changed while `pending` keeps sending scans to its old destination until this succeeds. This matters
most when retracting a URL, so check `list` afterwards.

`resync` covers only the signed-in user's codes.

## Tracing a request

- **API:** every error shows a correlation ID. Search for it in `/aws/lambda/qr-manager-<env>-api` and
  `/aws/lambda/qr-manager-<env>-render`.
- **Scan:** the function's log line and the access log share `request_id`, the access log's
  `x-edge-request-id`. CloudFront prefixes each function log line with the request ID, so that line is not
  valid JSON and needs text matching in CloudWatch.