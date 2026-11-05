# QR Manager

Creates and manages branded QR codes for 360 Balanced Living.

- **Static codes** encode a fixed URL, which can never change.
- **Dynamic codes** encode a short URL on `qr.360balancedliving.com`. The destination can change after
  printing, and scans are logged.

Phase I is the infrastructure, the API, and the `qr-admin` script. Phase II adds a Cognito-authorized web
UI. Both are thin clients of the same API, and every business rule stays on the server.

## Documents

| Document | Covers |
|---|---|
| [`docs/DESIGN.md`](docs/DESIGN.md) | Requirements, data model, architecture, flow charts, API contract, decisions, and risks |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Deploying, adding users, querying scans, and responding to alarms |
| [`docs/aws-architecture.drawio`](docs/aws-architecture.drawio) | The AWS schematic, also exported as a [PNG](docs/aws-architecture.drawio.png) |

## Layout

| Folder | Contents |
|---|---|
| `infra/` | CDK app in TypeScript: Edge, Auth, Api, and Analytics stacks, and the redirect function |
| `backend/` | API Lambda: `models/`, `cases/`, `adapters/`, `handlers/` |
| `render/` | Render Lambda, which draws and stores PNGs |
| `cli/` | The `qr-admin` client |

## Prerequisites

- [uv](https://docs.astral.sh/uv/), which installs Python and the Python dependencies
- Node.js, for CDK
- jq, for the user commands in `docs/OPERATIONS.md`
- AWS access through the `brian-admin` profile: `aws sso login --profile brian-admin`

## Using `qr-admin`

`--env` is required, so prod is never used by accident. The client reads `infra/outputs-<env>.json`, which
the deploy command writes.

```bash
cli/qr-admin --env sandbox login
cli/qr-admin --env sandbox create spring-flyer --dest https://360balancedliving.com/spring --campaign spring
cli/qr-admin --env sandbox create plain-link --kind static --dest https://360balancedliving.com/
cli/qr-admin --env sandbox update spring-flyer --dest https://360balancedliving.com/summer
cli/qr-admin --env sandbox list
cli/qr-admin --env sandbox show spring-flyer
cli/qr-admin --env sandbox png spring-flyer --logo flower
cli/qr-admin --env sandbox resync
```

`create` saves the PNG as `<code>-<logo>.png` unless `--output` is given. A blank `--campaign` adds no UTM
parameters. Codes are never deleted: to retire a dynamic code, point it at the home page.

## Running the checks

```bash
# Infrastructure
(cd infra && npm ci && npx tsc --noEmit && npx jest)

# API Lambda, including the layer rules
(cd backend && uv run pytest && uv run ruff check && PYTHONPATH=src uv run lint-imports)

# Render Lambda and client
(cd render && uv run pytest && uv run ruff check)
(cd cli && uv run pytest && uv run ruff check)
```

Do not run `npm audit fix --force`. The reported findings are in build-time tools, and forcing a fix can
change the pinned CDK versions.