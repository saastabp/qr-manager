/**
 * Packages a uv project as Lambda code, without Docker.
 *
 * Installs the locked dependencies as arm64 wheels, then copies src/ on top. Runs while
 * CDK builds the app, so uv must be installed wherever cdk runs.
 */
import { execFileSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as lambda from 'aws-cdk-lib/aws-lambda';

/** Runtime and architecture for every function. pyproject.toml must agree. */
export const PYTHON_RUNTIME = lambda.Runtime.PYTHON_3_14;
export const LAMBDA_ARCH = lambda.Architecture.ARM_64;

/** The wheel tag uv installs for. Must match LAMBDA_ARCH. Pillow needs 2_28 or newer. */
const UV_PLATFORM = 'aarch64-manylinux_2_28';

/** The repository root, relative to this file in infra/lib. */
const REPO_ROOT = path.resolve(__dirname, '..', '..');

/**
 * Build Lambda code from a uv project.
 *
 * @param project Folder name at the repository root, such as 'backend'.
 */
export function pythonBundle(project: string): lambda.Code {
  const projectDir = path.join(REPO_ROOT, project);

  return lambda.Code.fromAsset(projectDir, {
    // CDK hashes this folder to decide if the code changed. Keeping uv.lock inside it
    // means a dependency change triggers a redeploy.
    exclude: ['.venv', 'tests', '.pytest_cache', '.ruff_cache', '**/__pycache__'],
    bundling: {
      // Required by CDK but never used, because the local bundling below always runs.
      image: PYTHON_RUNTIME.bundlingImage,
      local: {
        tryBundle(outputDir: string): boolean {
          buildBundle(projectDir, outputDir);
          return true;
        },
      },
    },
  });
}

function buildBundle(projectDir: string, outputDir: string): void {
  // uv's output goes to stderr, so it never mixes with cdk's own output.
  const run = (args: string[]) =>
    execFileSync('uv', args, { cwd: projectDir, stdio: ['ignore', 2, 2] });

  const requirements = path.join(outputDir, 'requirements.txt');

  // 1. List the locked dependencies, leaving out the SDK the Lambda runtime provides.
  run([
    'export', '--frozen', '--no-dev',
    '--no-emit-package', 'boto3',
    '--no-emit-package', 'botocore',
    '-o', requirements,
  ]);

  // 2. Install arm64 wheels. --only-binary stops uv building anything for this machine's
  //    x86 chip, and --no-deps installs exactly the locked list.
  run([
    'pip', 'install',
    '-r', requirements,
    '--target', outputDir,
    '--python-platform', UV_PLATFORM,
    '--python-version', '3.14',
    '--only-binary', ':all:',
    '--no-deps',
  ]);

  // 3. Remove the temporary list, then copy the application code to the bundle root.
  fs.rmSync(requirements, { force: true });
  fs.cpSync(path.join(projectDir, 'src'), outputDir, {
    recursive: true,
    filter: (source) => !source.includes('__pycache__'),
  });
}