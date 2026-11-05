# QR Manager: design document

## 1. Purpose and scope

360 Balanced Living needs to create and manage branded QR codes of two kinds:

- **Static:** the destination URL is encoded in the image and can never change.
- **Dynamic:** the image encodes a short URL on `qr.360balancedliving.com`, and the destination can
  change after printing. Scans are logged for attribution.

| Phase | Delivers | Client |
|---|---|---|
| **Phase I** | Infrastructure and the complete server side | `qr-admin` script |
| **Phase II** | Cognito-authorized web UI for individual users | Browser |

**Guiding rule:** every business decision lives on the server. The script and the UI are both thin
clients of one API. Phase I's script exercises the same contract the UI will use, so Phase II adds a
client, not a rewrite.

## 2. Requirements

### Phase I (no UX)

| ID | Requirement |
|---|---|
| R1 | Create a static code that encodes a fixed destination |
| R2 | Create a dynamic code at `https://qr.360balancedliving.com/<code>` |
| R3 | Change a dynamic code's destination after printing; the printed image keeps working |
| R4 | Scanning a dynamic code issues a 302 to its destination, including UTM parameters |
| R5 | Unknown codes and bare `/` go to the home page, never a 404 |
| R6 | Codes are never deleted or reused |
| R7 | List and view codes |
| R8 | Produce and store the branded PNG for any code, to the existing brand spec |
| R9 | Record every scan of a dynamic code; query them with Athena |
| R10 | Every change is attributed to an authenticated person |
| R11 | Rebuild the live redirect data from the source of truth |
| R12 | Users see and change only their own codes; the code namespace stays global |

Non-functional: scans keep working if the API, Lambda, DynamoDB, or Cognito is down. Cost is
effectively $0 at about 100 scans a week. Infrastructure is CDK. Coding guidelines apply throughout.

### Phase II (UX)

| ID | Requirement |
|---|---|
| U1 | Individual users sign in through Cognito Managed Login |
| U2 | Screens for list, create, edit, and download, for both kinds of code |
| U3 | Users see scan statistics per code |
| U4 | Each user sees only their own codes and statistics |
| U5 | **The UX carries no business logic** (section 8) |
| U6 | Codes left `pending` are repaired automatically, without anyone rerunning a command |

## 3. Business cases

Cases live in `cases/`, and rules live there and nowhere else. Most cases are one module. BC5 has no
module of its own, because it is `change_code` with nothing to change. The steps that finish a code are
shared in `completion.py`.

| Case | Function | Rules |
|---|---|---|
| **BC1 Create code** | Register a static or dynamic code | Code is unused and not reserved. Destination is `https`. Campaign is optional for both kinds. Caller becomes owner. Dynamic is published to the redirect store. The default PNG is rendered. The code is ready only when every step succeeds. A repeat of the same request succeeds (see below) |
| **BC2 Change code** | Edit a code you own | Dynamic: destination, label, campaign. Static: label and nothing else. Kind never changes. Dynamic changes are republished |
| **BC3 View codes** | List or read your own codes | Returns status and allowed actions |
| **BC4 Get image** | Provide the PNG for a code you own | Static encodes its final URL. Dynamic encodes its short URL. Logo variant `full` or `flower`. `full` is rendered at creation; `flower` on first request, then served from S3. Refused while the code is `pending` |
| **BC5 Resync** | Finish or republish your codes | Completes whichever step is missing. One code, all pending, or all |
| **BC6 Scan stats** (Phase II) | Report scans for your codes | Bots excluded; uniques approximate |
| **BC7 Repair pending** (Phase II) | Scheduled job finishes every `pending` code | System job, so the ownership rule does not apply |

**Shared policies** (`cases/policies.py`): `authorize(actor, code, action)`, reserved codes
(`api`, `admin`, `health`), `https`-only destinations, the campaign format, the 900-byte limit on the
final URL, and the `allowed_actions` calculation. A campaign is blank, or follows the same format as a
code: lowercase letters, digits, and hyphens, up to 64 characters. That keeps it safe in the scan log
format. The URL limit leaves room for the campaign and JSON inside KVS's 1 KB value limit.

A destination must be printable ASCII, with no spaces, quotes, or backslashes, so anything else has to be
percent-encoded. A raw newline would otherwise reach the `Location` header, and JSON encoding would grow
non-ASCII text past the KVS limit after the size check had passed.

**Ownership rule, enforced from Phase I:** a user can act only on codes where `owner_sub` matches
their own. Another user's code returns 404, not 403, so the API does not reveal it exists. Code names
stay globally unique because they share one URL space, so creating a taken name still returns 409.

**Idempotent create:** if `POST /codes` finds the code already exists with the same owner and the same
fields, it treats the request as a retry: it finishes any incomplete step and returns success. This
covers a lost response, where the first write succeeded but the caller never heard. Any other
existing code returns 409.

**UTM rule** (`cases/redirect_url.py`): with a campaign, the final URL gains
`utm_source=qr&utm_medium=print&utm_campaign=<campaign>&utm_content=<code>`. With a blank campaign,
the final URL is the destination unchanged. That suits single-use codes: dynamic scans are still
counted per code in the logs, but the destination's own analytics see no UTM attribution.

There is no delete case, and no DELETE route. To retire a dynamic code, point it at the home page.

## 4. Data model

Data modeling is separate from the business cases. `models/` describes what things *are*. It contains
no rules, workflows, permissions, or I/O. Structural validity, such as the code's character format,
belongs here; decisions such as "reserved" or "static cannot change" do not.

```mermaid
erDiagram
    QR_CODE {
        string code PK "^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$"
        enum kind "static | dynamic, immutable"
        string destination "https URL entered by the user"
        string final_url "destination plus UTMs if campaign set"
        string label
        string campaign "blank, or same format as code"
        string owner_sub "stable Cognito id"
        string created_by "email"
        datetime created_at
        string updated_by "email"
        datetime updated_at
        int version "optimistic concurrency"
        bool synced "dynamic only"
        bool rendered "default PNG stored"
    }
```

| Model | Purpose |
|---|---|
| `QrCode` | The code entity above |
| `CodeKind` | `static` or `dynamic` |
| `CodeStatus` | Derived: `pending` until `rendered` is true and, for dynamic codes, `synced` is true; then `static` or `live` |
| `Actor` | Authenticated caller: `sub`, `email`, `groups` |

**Three shapes are kept distinct.** The domain model is `QrCode`. The storage shape (DynamoDB item)
is mapped in the repository. The API shape (request and response schemas) is mapped in the handlers.
A storage or API change does not touch the model.

**Storage:** table `qr-manager-<env>-codes`, PK `code`, with GSI `by-owner` (`owner_sub`, `created_at`) for
"my codes". On-demand in both environments; PITR, deletion protection, and RETAIN in prod only.
**DynamoDB is the source of truth.**
KVS is a read projection of dynamic codes only. The key is `code`, and the value is JSON holding what a
scan needs: `{"url": "<final_url>", "campaign": "<campaign>"}`.

## 5. Architecture

The AWS schematic is in [`aws-architecture.drawio`](aws-architecture.drawio).

```mermaid
flowchart LR
    phone[Phone scan] --> cf[CloudFront<br/>qr.360balancedliving.com]
    cf --> fn[CloudFront Function]
    fn --> kvs[(KeyValueStore)]
    fn -->|302| dest[Destination]
    cf --> logs[(S3 logs)] --> athena[Athena]

    client[qr-admin / Phase II UI] --> cog[Cognito]
    client --> api[HTTP API + JWT authorizer]
    api --> lambda[Lambda]
    lambda --> ddb[(DynamoDB<br/>source of truth)]
    lambda -->|publish dynamic codes| kvs
    lambda -->|render and wait| render[Render Lambda]
    render --> images[(S3 images)]
    lambda -->|check, presign| images
    client -->|download with presigned URL| images
```

- **Scan path has no dependency on the API side.** Static codes never touch our infrastructure.
- **All in us-east-1**, unlike the us-west-2 siblings. The cert, KVS, and log delivery belong there,
  and the SES identity is there, so one region removes cross-region references.
- **`qr.` stays redirect-only**, because any path there is a code. The UI gets
  `qr-admin.360balancedliving.com`, with `/api/*` proxied to the API, which avoids CORS.

| Stack (CDK TypeScript) | Contents | Why separate |
|---|---|---|
| `qr-manager-Edge` | Cert, KVS, function and log group, distribution, Route 53 alias, log bucket, log delivery, KVS failure and function failure alarms, SNS alert topic | Printed codes depend on it, so API deploys must not touch it |
| `qr-manager-Auth` | Invite-only pool (RETAIN in prod), SES email, `qr-manager-<env>-cli` client (`disableOAuth: true`) | Holds identities |
| `qr-manager-Api` | DynamoDB table (RETAIN in prod), API Lambda, render Lambda, private images bucket, HTTP API, publish or render failure alarm | Changes most often |
| `qr-manager-Analytics` | Glue database, `scan_logs` table, `scans` view, Athena workgroup `qr_analytics_<env>` | Schema comes from a real log file; fixes must not touch Edge |

The names above are logical. Each environment deploys its own copy as `qr-manager-<env>-<Stack>`, for
example `qr-manager-sandbox-Edge`.

### Environments

All four stacks live in one CDK `Stage` class, and each stack and named resource has an explicit name
that includes the environment. `bin/app.ts` creates the stage twice, so both copies share one
definition and differ only by config.

| Setting | `sandbox` | `prod` |
|---|---|---|
| Domain | `qr-sandbox.360balancedliving.com` | `qr.360balancedliving.com` |
| Cognito pool, KVS, table, buckets, Athena workgroup | Own copy | Own copy |
| Removal policy | Destroy, no deletion protection | RETAIN, deletion protection |
| DynamoDB PITR | Off | On |
| CloudWatch log retention | 1 month | 3 months |
| Alarm emails | Off | On |

Every change deploys to sandbox first, including the risky first checks in section 11. Sandbox has a
real domain so test codes can be scanned with a phone. The CLI selects an environment with
the required `--env` option, so prod is never used by accident. Deploys are by hand:
`npx cdk deploy 'sandbox/*' --profile brian-admin --outputs-file outputs-sandbox.json`. The CLI reads
`infra/outputs-<env>.json`; each environment has its own file because CDK overwrites the file on each
deploy.

This repository is public, so it holds no account or zone IDs. The account comes from `--profile`, and the
Route 53 zone is looked up by name. CDK caches that lookup in `infra/cdk.context.json`, which git ignores.
The cost is that nothing pins the account in code, so the zone lookup is the guard: a profile for another
account does not find the zone, and the deploy stops.

Confirmed in sandbox: the API Lambda's role credentials and the bundled `awscrt` work with the KVS
SigV4A API, so the account-wide session token setting was not needed.

Both copies are in the same account, so they share the CloudFront free tier. At this volume the
sandbox should add almost nothing to the cost.

### Later option: deployment pipeline

**Not built.** Add it after Phase I is running. Because the stacks are already in a `Stage` with
explicit names, adding it does not rename or replace anything.

```mermaid
flowchart LR
    P[git push to main] --> S[CodePipeline, via existing GitHub connection]
    S --> B[CodeBuild: tests and cdk synth]
    B --> M[Pipeline updates itself if needed]
    M --> SB[Deploy sandbox]
    SB --> A[Manual approval]
    A --> PR[Deploy prod]
```

This would use CDK Pipelines, in a `qr-manager-Pipeline` stack deployed by hand once. The build needs uv
installed. The manual approval keeps prod deploys a deliberate decision. GitHub Actions would not
trigger the pipeline; it would only run checks on pull requests.

### 5.1 Flow charts

**Scan a dynamic code.** This path uses no API, Lambda, DynamoDB, or Cognito.

```mermaid
flowchart TD
    S[Phone scans printed code] --> CF[CloudFront receives request]
    CF --> P{Path is bare /?}
    P -->|Yes| HOME[302 to home page<br/>outcome: home]
    P -->|No| V{Valid code format?}
    V -->|No| INV[302 to home page<br/>outcome: invalid]
    V -->|Yes| K{Found in KVS?}
    K -->|Yes| HIT[302 to final URL<br/>outcome: hit]
    K -->|No, or read failed| FB[302 to home page<br/>outcome: fallback]
    HOME --> L[Access log to S3]
    INV --> L
    HIT --> L
    FB --> L
    FB -.->|Read failed, not a miss| AL[Alarm email]
```

**Create a code.** Validation and ownership happen before anything is stored. The call is synchronous,
and the code is ready only when the redirect is published and the PNG exists. The first failed step
stops the sequence; a retry completes only what is missing.

```mermaid
sequenceDiagram
    actor U as User
    participant C as qr-admin or UI
    participant A as API and JWT authorizer
    participant B as BC1 Create code
    participant D as DynamoDB
    participant K as KVS
    participant R as Render Lambda and S3

    U->>C: code, kind, destination, campaign (optional)
    C->>A: POST /codes with ID token
    A->>B: Authenticated request
    B->>B: Apply rules, build final URL
    alt Rule broken
        B-->>C: 400 with field errors
    else Valid
        B->>D: Conditional put, status pending
        alt Name taken by a different request
            D-->>C: 409
        else New code, or a retry with the same owner and fields
            opt Dynamic code, not yet synced
                B->>K: Publish final URL
            end
            opt Not yet rendered
                B->>R: Render default PNG and wait
            end
            alt All steps succeeded
                B->>D: Mark complete if version matches
                B-->>C: 201 (200 for a retry), status live or static
            else A step failed
                B-->>C: 502 naming the step, status pending
            end
        end
    end
    C->>A: GET /codes/{code}/image
    A-->>C: Presigned URL
```

**Get the image.** Nothing renders in the background. The default PNG already exists by this point,
so this usually just returns a URL. The render Lambda runs here only for the `flower` variant's first
request. A `pending` code is refused, so nobody can print a code that is not ready.

```mermaid
flowchart TD
    G[GET /codes/code/image] --> O{Caller owns it?}
    O -->|No, or missing| N[404]
    O -->|Yes| P{Status pending?}
    P -->|Yes| NR[409 not ready]
    P -->|No| X{PNG in S3?}
    X -->|Yes| URL[Return presigned URL]
    X -->|No| R[API Lambda invokes render Lambda and waits]
    R --> W[Render Lambda writes PNG to S3]
    W -->|Succeeded| URL
    W -->|Failed| E[502, safe to retry]
    URL --> D[Client downloads from S3]
```

**Change a code.** The printed image never changes; only the stored destination does.

```mermaid
flowchart TD
    R[PATCH /codes/code] --> O{Caller owns it?}
    O -->|No, or missing| N[404]
    O -->|Yes| T{Static, and destination<br/>or campaign changing?}
    T -->|Yes| REJ[400 refused]
    T -->|No| W[Save, version + 1]
    W --> DY{Dynamic?}
    DY -->|No| OK[200]
    DY -->|Yes| PUB[Publish to KVS<br/>re-reading the latest value]
    PUB -->|Succeeded| SY[synced=true if version matches] --> OK
    PUB -->|Failed| E[502, status pending<br/>fix with resync]
```

**Static codes** have no flow of their own after creation: the phone goes straight to the
destination, so none of our infrastructure is involved and no scans are recorded.

### Redirect function

Viewer-request function, JS runtime 2.0, using `cf.kvs()` with no ID. It checks the code format
before reading KVS, so `/favicon.ico` and scanner noise skip the lookup. The whole handler is wrapped
so any failure still returns a 302 to the home page, never a 5xx. It sets `cache-control: no-store`.
A found code's KVS value is parsed as JSON. A value that cannot be parsed goes home as a `fallback`, and
because it is not `KeyNotFound`, it raises the alarm.

`cf.logCustomData` always records three parts: `<code>:<campaign>:<outcome>`, with an empty campaign
when there is none, such as `claim-yourself:discovery:hit` or `one-off::hit`. The outcome is `hit`,
`fallback`, `invalid`, or `home`. The `scans` view takes the code from `cs-uri-stem`, because custom data
can be absent at embedded POPs.

**The runtime is stricter than Node, which is where the tests run.** In sandbox, `await` inside a call's
arguments stopped the whole file loading, so every request returned a 503 while every test passed. CDK
publishes functions straight to live, so `aws cloudfront test-function` must run after every Edge deploy.
A guard test covers this one case, and the `function-errors` alarm covers the rest.

### Publishing to KVS

Synchronous, inside BC1, BC2, and BC5: write DynamoDB with `synced=false` and `version + 1`. Then, on
each attempt, read the store ETag, read the item with `ConsistentRead`, and `PutKey` its final URL and
campaign. Set
`synced=true` only if `version` still matches. Re-reading stops a retry overwriting a newer
destination with a stale one. If `version` has changed, the next attempt republishes the newer value
even if it is marked as synced, because a slower writer may have overwritten it in KVS. On failure the
API returns 502 and the code stays `pending`, visibly.

Because DynamoDB is always written first, KVS can be out of date but never holds a code DynamoDB does
not know about. Scans always get a 302, so a printed code never shows an error page.

| What fails | State afterwards | Effect on scans | Recovery |
|---|---|---|---|
| First DynamoDB write | Nothing changed | New code: goes home. Existing code: unchanged | Caller gets an error and retries |
| KVS write, after its own retries | DynamoDB has the new value, status `pending` | New code: goes home. Changed code: still goes to the old destination | 502, then `update` or `resync` |
| Render, new codes only | Redirect is published, no PNG, status `pending` | None yet: with no PNG, nothing can be printed or scanned | 502, then repeat `create` or run `resync` |
| Lambda dies between the steps | Status stays `pending` | Same as the step that did not run | Caller sees a timeout or 5xx, then the same recovery |
| Final status write | All steps done, status stays `pending` | None | `resync` clears it |

**Repairing `pending` codes:** in Phase I, the caller sees the error, and an alarm on publish or render
failures emails the owner so a failure cannot go unnoticed. Phase II adds BC7, a scheduled job, because
a browser user can leave without retrying.

**Updates do not render.** An existing code's PNG never changes, so `PATCH` only touches DynamoDB and
KVS. No confirming read is needed after a write: KVS's success response and the render Lambda's result
are the confirmation. `live` means KVS accepted the value; edge locations catch up shortly afterwards.
A sandbox test took 23 seconds, from one edge location and one sample.

### Rendering and storing PNGs

A code's PNG never changes: a dynamic code encodes its short URL rather than its destination, and a
static code's destination cannot change. So the image depends only on the code and logo variant.
Repeating a render writes the same image to the same key, which makes retries safe.

The render Lambda does one thing: create and store the PNG. It receives `encoded_url`, `logo`, the S3
key, and the correlation ID, and it has no DynamoDB access and no business rules. PNGs are stored at
`qr-images/<code>/<logo>.png` in a private bucket. They can be regenerated, so the bucket has no
special retention. Clients download with a presigned URL valid for 5 minutes, which keeps image bytes
out of the API and avoids Lambda's 6 MB response limit.

The API Lambda invokes it synchronously. A local test of the existing renderer took 0.34 to 0.82 s,
used about 150 MB, and produced a 177 KB PNG, so a synchronous call fits well inside API Gateway's
30 s limit. Lambda will be slower than that test machine; see section 11.

### Later option: asynchronous create

**Not built.** Consider it only if measured `POST /codes` times approach the API Gateway limit, for
example if rendering becomes much heavier.

`POST /codes` would still validate and claim the code, so 409 stays immediate. It would then return
`202` with status `pending`, a background Lambda would finish the steps, and clients would poll
`GET /codes/{code}` until `status` changes. DynamoDB already holds that status. The cost is a failed
state, a failure destination for the background Lambda, the repair job moving into Phase I, and a
polling loop in both clients.

SNS is not suitable for returning results: it pushes to endpoints such as email, SQS, Lambda, or a
public HTTPS URL, so a script or browser cannot subscribe to it directly.

### Later option: selectable logos

**Not built.** A future UX may let users choose the logo in a QR code. `logo` is already a name passed
through the API, the render Lambda, and the S3 key, so this does not need a redesign. It would need:

- A list of logos stored outside the code, replacing `LOGO_VARIANTS` and the bundled asset files.
- A `logo` field on `QrCode`, so the chosen logo replaces `DEFAULT_LOGO` at creation.
- A rule that a logo name is never reused for a different image, because PNGs are stored by logo name
  and changing the file behind a name would leave old PNGs in place.

If users can upload their own logos, uploads also need checks, because a low-contrast logo or unusual
file could stop a code scanning.

### Scan logging

Standard logging v2 to S3 as JSON, under `qr-logs/year=/month=/day=`. Destination ARN is
`<bucket>/qr-logs`, suffix `{yyyy}/{MM}/{dd}`, with an explicit `delivery.logs.amazonaws.com` bucket
statement. Fields: `timestamp(ms)`, `x-edge-request-id`, `c-ip`, `cs-uri-stem`, `sc-status`,
`cs(User-Agent)`, `c-country`, `x-edge-result-type`, `time-taken`, `viewer-request-log-data`.

Confirmed in sandbox: S3 files arrive within minutes, and the JSON keys match these field names. All
values are strings. CloudFront prefixes each function log line, so a line reads `<id> <json>`
and is not valid JSON. CloudWatch filters on it must use text matching.

### Querying scans

Athena queries the log files in place, so there is no database to run. Each environment has its own
database, `qr_manager_<env>`, and workgroup, `qr_analytics_<env>`. The workgroup stops any query reading
more than 100 MB, and clients cannot override that. Results go to `athena-results/` in the log bucket, so
they expire with the logs.

The catalog has two objects, so the rules for reading logs live in one place:

| Object | Kind | Holds | Used for |
|---|---|---|---|
| `scan_logs` | Glue table | Every request, exactly as logged, with `outcome_data` unparsed | Noise and troubleshooting |
| `scans` | View over `scan_logs` | Real scans only, with named columns | All scan reporting |

**`scan_logs`** finds the `year=/month=/day=` folders by partition projection, so no job has to register
them. **`scans`** has these columns:

| Column | Source |
|---|---|
| `code` | `cs-uri-stem`, without the leading `/` |
| `campaign` | The second part of `outcome_data`; NULL when blank or missing |
| `outcome` | The third part of `outcome_data`; NULL when missing |
| `scanned_at` | `timestamp(ms)`, in `Pacific/Honolulu`, set once in the view |
| `client_ip`, `country`, `user_agent` | Passed through |
| `year`, `month`, `day` | Passed through, in UTC. Filtering on them keeps queries cheap |

Confirmed in sandbox: the table reads every field, including the mapped names `cs(User-Agent)` and
`timestamp(ms)`, and the view returns `campaign` and local times from real phone scans. CDK has no view
construct, so `scans` is a Glue table of type `VIRTUAL_VIEW` holding Athena's encoded definition.
`scanned_at` is formatted as text before its cast, because casting a converted time directly gave back
UTC. Three findings set the view's rules:

- **Most traffic is noise.** Only 8 of 292 function responses were `hit`. The rest were `fallback`,
  `invalid`, and `home`, mostly from bots, and the `fallback` count also includes test misses.
- **A scan is a function response that is a `hit` or has no outcome, on a path other than `/`, from
  something other than a known bot.** The missing outcome case keeps scans from embedded POPs, which can
  omit custom data. None of those 292 lacked an outcome, but the sample is small.
- **Values need care.** User agents are URL-encoded, and missing custom data is a literal `-`. `Redirect`
  rows are the HTTP to HTTPS 301, and `Error` rows are refused requests, so both are excluded.

Example queries are in [`OPERATIONS.md`](OPERATIONS.md).

## 6. Code segregation

```mermaid
flowchart TD
    H[handlers: HTTP only] --> C[cases: business rules]
    C --> M[models: data shapes]
    C --> P[cases/ports: interfaces]
    A[adapters: DynamoDB, KVS, S3, render invoker] -. implements .-> P
    A --> M
    B[bootstrap: wiring] --> H
    B --> A
    A -. invokes .-> R[render: separate project, no domain code]
```

| Layer | Contains | Must not contain |
|---|---|---|
| `models/` | Data shapes and structural validity | Rules, workflows, permissions, I/O, AWS or HTTP types |
| `cases/` | Business case modules, `completion.py`, `redirect_url.py`, `policies.py`, `ports.py`, `errors.py` | boto3, HTTP types, DynamoDB item shapes |
| `adapters/` | `dynamodb_codes.py`, `kvs_publisher.py`, `s3_images.py`, `render_invoker.py`; item-to-model mapping | Business rules |
| `handlers/` | Routes, request and response schemas, error-to-status mapping | Business rules, AWS clients |
| `bootstrap.py` | Wires adapters into cases | Everything else |
| `common/` | Logger, correlation ID | Domain knowledge |
| `render/` (separate project) | Render handler, PNG drawing, logo assets | Anything from `backend/`: models, cases, DynamoDB |

**Enforced, not just documented.** Four `import-linter` contracts make `PYTHONPATH=src uv run lint-imports`
fail if a layer imports something it must not:

- `models` must not import `cases`, `adapters`, `handlers`, the AWS SDK, or Powertools.
- `cases` must not directly import `adapters`, `handlers`, the AWS SDK, or Powertools. Indirect imports
  are allowed, because cases log through `common/logger.py`, which uses Powertools.
- `handlers` must not import `adapters` or the AWS SDK.
- `adapters` must not import `handlers`.

**Testing follows the layers.** Cases are tested with in-memory fakes of the ports, so every
business rule has a fast test with no AWS. `moto` covers the DynamoDB adapter only.

```
qr-manager/
  README.md  CLAUDE.md  .gitignore
  docs/  DESIGN.md  OPERATIONS.md  aws-architecture.drawio
  infra/  bin/app.ts  lib/{config,stage,edge-stack,auth-stack,api-stack,analytics-stack,python-lambda}.ts
          lib/edge/{redirect-service,scan-logging,alerts}.ts  lib/api/
          lib/cloudfront-functions/redirect.js  test/
  backend/  pyproject.toml  uv.lock  tests/
  backend/src/  api_handler.py  bootstrap.py
                models/  cases/  adapters/  handlers/  common/
  render/  pyproject.toml  uv.lock  tests/
  render/src/  render_handler.py  png_renderer.py  assets/
  cli/  pyproject.toml  uv.lock  qr-admin  qr_admin.py  tests/
```

`backend/`, `render/`, and `cli/` are separate uv projects. Each Lambda's `uv.lock` stays inside its
own directory because CDK hashes that directory; a root lockfile would let a dependency bump skip the
Lambda redeploy. The separate `render/` project keeps Pillow out of the API bundle and means render
code cannot import business code.

Larger stacks are short parents that assemble custom constructs from a folder of the same name, such
as `lib/edge/`. Small stacks stay as single files until they need more. Split a stack before its first
deploy: a construct's position is part of each resource's CloudFormation ID, so moving a deployed
resource makes CloudFormation try to replace it.

`cli/qr-admin` is a small shell wrapper that runs `qr_admin.py` through uv. uv is the only prerequisite,
because it installs Python and the dependencies itself.

### Copied code

**`speaker-tracker` and `ghl` are copy sources only.** Nothing imports, references, or builds against
them, and this repo has no path, package, or runtime dependency on either. Copied files are owned here
from then on and adapted freely.

| Copied from | To | Changes |
|---|---|---|
| `ghl/scripts/make_qr.py` | `render/src/png_renderer.py` | Argument parsing removed; returns bytes; NumPy docstrings. Original untouched |
| `speaker-tracker/.../python-lambda.ts` | `infra/lib/` | Python 3.14; takes the project directory as a parameter, for `backend/` and `render/`; wheel tag `aarch64-manylinux_2_28`, because Pillow 13 has no `manylinux2014` wheel |
| `speaker-tracker/.../auth-stack.ts` | `infra/lib/` | Pool settings and SES email; no OAuth client until Phase II |
| `speaker-tracker/.../common/logger.py` | `backend/src/common/` | Service name |
| `speaker-tracker/.../cdk.json` | `infra/` | Keeps the deliberate `strong` cross-stack setting |
| Test pattern for shipped function bytes | `infra/test/` | Strips `import`, injects a fake `cf` |

Versions pinned as there: `aws-cdk-lib` 2.261.0, `aws-cdk` 2.1132.0, run with `npx`.

## 7. API contract

All routes sit behind the JWT authorizer, and this contract serves both the script and the UI.

| Route | Case | Result |
|---|---|---|
| `POST /codes` | BC1 | 201, or 200 for a repeat of the same request; 409 if the code is otherwise taken; 502 naming the step if publishing or rendering fails |
| `GET /codes` | BC3 | The caller's codes |
| `GET /codes/{code}` | BC3 | One code; 404 if not the caller's |
| `PATCH /codes/{code}` | BC2 | Updated code; 404 if not the caller's. An empty body finishes any incomplete step and republishes |
| `GET /codes/{code}/image?logo=full` | BC4 | `{ "url": "<presigned>", "expires_in": 300 }`; 404 if not the caller's; 409 while `pending`; 502 if rendering fails |

BC5 needs no route of its own in Phase I: `qr-admin resync` sends an empty `PATCH` per code, and
`qr-admin update <code>` with no options does the same for one code.

```json
{
  "code": "bldc-flyer", "kind": "dynamic", "status": "live",
  "encoded_url": "https://qr.360balancedliving.com/bldc-flyer",
  "destination": "https://360balancedliving.com/bldc",
  "final_url": "https://360balancedliving.com/bldc?utm_source=qr&...",
  "label": "BLDC flyer", "campaign": "bldc",
  "allowed_actions": ["update", "download"],
  "updated_by": "user@example.com", "updated_at": "2026-11-04T17:00:00Z"
}
```

```json
{
  "error": { "type": "validation", "message": "Code is reserved",
             "fields": [{ "field": "code", "message": "Code is reserved" }] },
  "correlation_id": "..."
}
```

## 8. UX boundary (Phase II)

The UI displays what the API returns and sends what the user enters. Server responses are designed so
it never has to decide anything:

| The UI needs to know | It reads | It must not |
|---|---|---|
| Which buttons to show | `allowed_actions` | Infer from role or kind |
| Whether a code is ready | `status` | Work it out from other fields |
| What URL a code encodes | `encoded_url`, `final_url` | Build URLs or UTMs |
| Whether input is valid | `error.fields` | Duplicate validation rules |
| The QR image | The `url` from `GET .../image` | Render QR codes in the browser |
| Statistics | Stats endpoint | Filter bots or aggregate |

Purely presentational checks are allowed, such as marking a field required. The server still decides.
The `qr-admin` script follows the same rules, so it proves the boundary before the UI exists.

**Phase II adds, with no changes to `models/` or existing cases:** SPA client, Managed Login domain
and branding, a `Frontend` stack at `qr-admin.`, another `jwtAudience` entry, BC6 with its route and a
daily-counter Lambda (backfilled from retained logs, because Athena is too slow for a page load), and
BC7 with its schedule. Ownership is already enforced, so no access rules change.

## 9. Security and logging

- **Auth:** invite-only pool. The script uses `USER_PASSWORD_AUTH` on the `qr-manager-<env>-cli` client,
  prompted with `getpass`, and sends the ID token, because access tokens have no `email` claim. Tokens are cached in
  `~/.config/qr-manager/tokens-<env>.json` with mode 0600. The first user is created with
  `admin-create-user` and `admin-set-user-password --permanent`. `qr-admin --verbose` raises only the
  script's own logger, because debug logging in botocore prints the password at sign-in.
- **API logging:** pairable entry and exit logs with correlation ID and `duration_ms`; every error
  response, 4xx included, logs its body and stack trace at ERROR. Alarms match log text, not the level, so
  these lines never page anyone. Own lines for DynamoDB writes, KVS calls with latency, and
  authorization decisions. The API Lambda also logs render invocations with latency, and passes the
  correlation ID so the render Lambda's entry and exit logs pair with the request.
  Lazy `%s` formatting. The raw event is never logged, as it carries the JWT.
- **Exception to the logging rule:** CloudFront Functions freeze `Date`, so `duration_ms` cannot be
  measured. The function writes one `console.log` line with `event.context.requestId`, code, and
  outcome; the access log supplies `time-taken`, joined on `x-edge-request-id`.
- **Style:** braces and own-line bodies everywhere; NumPy docstrings on all public Python.
- **Alarms:** there are three, all at a threshold of 1, because at this scan volume an outage may produce
  only a few failures. All use the SNS topic in the Edge stack, which is for operational alerts only.
  - `kvs-failures`: the catch-all turns errors while the function runs into quiet redirects to home, so a
    metric filter counts `fallback` lines, excluding `KeyNotFound`. Misses are excluded because bots cause
    them. A valid code missing from KVS does not alarm, but a failed publish already shows as `pending`
    and raises `step-failures`.
  - `function-errors`: a function that cannot load or run returns a 503 and writes no logs, so the catch-all
    and `kvs-failures` both miss it. This alarm uses CloudFront's `FunctionExecutionErrors` and
    `FunctionThrottles` metrics, which recorded the sandbox outage. They arrive a few minutes late.
  - `step-failures`: a metric filter on the API Lambda's log group catches any KVS publish or render
    failure.
- **Images:** the bucket is private with public access blocked. Only the render Lambda can write, and
  the API Lambda can read and presign. The API Lambda alone may invoke the render Lambda.

## 10. Decisions

| Topic | Decision |
|---|---|
| Ownership | Users see and change only their own codes, enforced from Phase I |
| Campaign | Optional on both kinds; blank means no UTMs |
| Raw IPs | Keep `c-ip` for approximate uniques, expire logs at 400 days, never copy IPs elsewhere |
| Inviting users (Phase II) | AWS console at first; an in-app admin screen would be a new case |
| Cognito pool | Dedicated pool, as every app here has, so a separate login |
| PNG storage | Stored in S3 by a single-purpose render Lambda. The default PNG is rendered at creation, because a code with no PNG cannot be scanned |
| Ready status | `live` or `static` only when the redirect is published (dynamic) and the default PNG exists |
| Create call | Synchronous; `202` plus polling is kept as a later option in section 5 |
| Environments | `sandbox` and `prod`, from one CDK `Stage` with explicit names |
| Deployment | By hand for Phase I; CDK Pipelines is the later option in section 5, using the existing GitHub connection |
| CLI language | Python run through uv with a shell wrapper; a pure shell script would still need `curl` and `jq` |
| S3-triggered DynamoDB write | Rejected. The caller would lose the synchronous 409, failures would be silent, S3 events would still need idempotent writes, and business data would travel in object metadata |
| SNS for returning results | Rejected. Scripts and browsers cannot subscribe, and a shared topic would break per-user isolation |
| Campaign in scan logs | Carried as data in the KVS value and logged per scan, so a scan keeps the campaign live at that moment. Reading it from the URL's UTMs was rejected because it ties the function to the UTM format |
| DynamoDB export joined in Athena | Rejected. It needs a scheduled job, and shows a code's current campaign rather than the one live at the scan |
| Scan reporting | Through the `scans` view, so bot and parsing rules are not repeated in each query |
| Pre-launch reset | One-time exception to R6, before any code is printed in volume: stored data is emptied in both environments so every record uses the new formats. `claim-yourself` is recreated under the same name, so its existing PNG keeps working |

**Known gap, for Phase II:** owner-only access means no user can act across all users. BC7 repairs
`pending` codes as a system job, but a full KVS rebuild for every owner and reassigning codes when
someone leaves would both be new cases behind an `admins` group. Phase I has one user, so neither is
needed yet.

## 11. Risks

- **`POST /codes` waits for a render.** Measured in sandbox: 5.6 s for a create with both Lambdas cold,
  1.4 s for the render itself, 0.2 s for the KVS write, and 0.8 s for an image request. That is well
  inside the 30 s limit. The render Lambda gets 1024 MB and a 10 s timeout, so a stuck render fails
  quickly. If times ever approach the limit, use the asynchronous option in section 5.
- **A `pending` change leaves the old destination live.** This matters most when retracting a URL.
  Phase I relies on the 502 and the failure alarm; BC7 automates the repair in Phase II.
- **A real KVS read failure has not been observed.** Both a miss and a failure go home. A miss logs
  `Error: KeyNotFound`, confirmed in sandbox, so anything else is treated as a failure.
- **Failure paths are proven by tests, not on real AWS.** No real publish or render failure has happened
  in sandbox, so the `step-failures` alarm has not fired on its own. The `function-errors` alarm has not either,
  although its metrics recorded a real outage. Delivery is proven: forcing that alarm with `set-alarm-state` in
  prod sent the email. That test skips the metrics, so it does not show that a real failure trips the alarm.
- **Function deploys go live immediately**, hence the tests, the separate Edge stack, deploying to sandbox
  first, and running `test-function` after every Edge deploy.