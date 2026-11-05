# QR Manager

Static and dynamic QR codes for 360 Balanced Living. Read `docs/DESIGN.md` before changing behavior, and
`docs/OPERATIONS.md` for deploys and scan queries.

## Rules that are easy to break

- **No business logic in clients.** `qr-admin` and the Phase II UI display what the API returns. Rules live
  in `backend/src/cases/`.
- **Layers are enforced.** `models/` holds data shapes only. `cases/` must not import `adapters`,
  `handlers`, `boto3`, or HTTP types. Run `uv run lint-imports` in `backend/`.
- **`render/` stays separate.** It must not import anything from `backend/`.
- **`speaker-tracker` and `ghl` are copy sources only.** Never import from them or depend on them.
- **Codes are never deleted.** There is no delete case and no DELETE route.
- **DynamoDB is the source of truth.** KVS is a read projection of dynamic codes.
- **The redirect function always returns a 302.** A printed code must never show an error page.

## CDK

- All stacks are in us-east-1, in one `QrManagerStage`, built for `sandbox` and `prod` from `lib/config.ts`.
  Both share one account, so every named resource must include the environment.
- A construct's position is part of its CloudFormation ID. Moving a deployed resource makes CloudFormation
  try to replace it, so split a stack before its first deploy.
- Keep `@aws-cdk/core:defaultCrossStackReferences` as `strong`. A test guards this.
- Comment each section of infrastructure files and explain CDK concepts.
- Tests build stages with `new App({ context: { 'aws:cdk:bundling-stacks': [] } })` to skip Lambda bundling.

## Checks

```bash
cd infra && npx tsc --noEmit && npx jest
cd backend && uv run pytest && uv run ruff check && uv run lint-imports
cd render && uv run pytest && uv run ruff check
cd cli && uv run pytest && uv run ruff check
```

Re-export the AWS schematic after changing it:

```bash
timeout 90 drawio --no-sandbox -x -f png -e -b 10 -o docs/aws-architecture.drawio.png docs/aws-architecture.drawio
```