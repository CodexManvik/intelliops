# Requirements Document

## Introduction

The existing CI pipeline (`ci.yml`) runs five jobs — lint, test, frontend-build, slim-boundary, and compose-smoke — but has opportunities for improvement in three areas:

1. **Parallelism**: `lint` unnecessarily gates `test`, making the pipeline slower than it needs to be. `frontend-build` already runs independently; `lint` and `test` should too.
2. **Security scanning**: There is no automated check for known vulnerabilities in Python dependencies, frontend dependencies, or the Docker images produced by the project.
3. **Docker image publishing**: Successful builds on `master` never produce a versioned artifact. Docker images should be built and pushed to GitHub Container Registry (GHCR) so that deployments and local testing can reference immutable, CI-verified images.

These improvements keep the pipeline fast, safe, and reproducible without changing existing job logic.

## Glossary

- **CI_Pipeline**: The GitHub Actions workflow defined in `.github/workflows/ci.yml`.
- **lint**: The existing job that runs `ruff check` and `ruff format --check` against Python source.
- **test**: The existing job that runs `pytest` (excluding `postgres` and `kafka` marks).
- **frontend-build**: The existing job that runs `npm ci && npm run build` in the `frontend/` directory.
- **slim-boundary**: The existing job that verifies base-image services import without heavy ML/k8s dependencies.
- **compose-smoke**: The existing job that starts all services via Docker Compose and checks health/readiness endpoints.
- **Security_Scanner**: The set of scanning jobs added by this feature (dependency scanning + image scanning).
- **Image_Publisher**: The new job that builds and pushes Docker images to GHCR.
- **GHCR**: GitHub Container Registry (`ghcr.io`), the target registry for published images.
- **pip-audit**: A Python tool that audits `uv`-managed dependencies against known vulnerability databases.
- **Trivy**: An open-source vulnerability scanner that inspects container images for OS and library CVEs.
- **npm audit**: The built-in Node.js command that checks frontend dependencies for known vulnerabilities.

## Requirements

### Requirement 1: Parallel Lint and Test Execution

**User Story:** As a developer, I want lint and tests to run in parallel, so that I get faster feedback on pull requests without waiting for one to complete before the other starts.

#### Acceptance Criteria

1. WHEN a push or pull-request event triggers the CI_Pipeline, THE CI_Pipeline SHALL execute the `lint` job and the `test` job concurrently with no `needs` dependency between them.
2. WHEN the `lint` job and the `test` job run concurrently, THE CI_Pipeline SHALL require both `lint` and `test` to complete successfully before `slim-boundary` or `compose-smoke` are allowed to start; specifically, both `slim-boundary` and `compose-smoke` SHALL declare a `needs` dependency on both `lint` and `test`.
3. WHEN either the `lint` job or the `test` job fails, both jobs SHALL reach a terminal state before the workflow concludes, and the workflow conclusion SHALL be `failure`.
4. THE CI_Pipeline SHALL execute `frontend-build` with no `needs` dependency on `lint`, `test`, or any other job, preserving its independent parallel execution.

---

### Requirement 2: Python Dependency Vulnerability Scanning

**User Story:** As a developer, I want Python dependencies scanned for known vulnerabilities on every CI run, so that security issues in transitive dependencies are caught before code is merged.

#### Acceptance Criteria

1. WHEN a push or pull-request event triggers the CI_Pipeline, THE Security_Scanner SHALL run `pip-audit` against the resolved dependency set declared in `uv.lock`.
2. WHEN `pip-audit` identifies one or more vulnerabilities with a known fix available, THE Security_Scanner SHALL exit with a non-zero status code, causing the Python dependency scan job to fail.
3. WHEN `pip-audit` exits with a non-zero status code for any reason (fixable vulnerabilities found, tool error, or other failure), THE Security_Scanner SHALL cause the CI_Pipeline job to fail immediately.
4. WHEN `pip-audit` finds no vulnerabilities or only vulnerabilities with no fix available, THE Security_Scanner SHALL exit with a zero status code.
5. THE Security_Scanner SHALL run the Python dependency scan in a job that declares no `needs` dependency on the `lint` or `test` jobs, so that scan results are available even if those jobs fail.
6. IF `uv.lock` is absent or unreadable at the time the Python dependency scan runs, THEN THE Security_Scanner SHALL exit with a non-zero status code and cause the CI_Pipeline job to fail immediately.

---

### Requirement 3: Frontend Dependency Vulnerability Scanning

**User Story:** As a developer, I want frontend npm dependencies scanned for known vulnerabilities on every CI run, so that supply-chain risks in JavaScript packages are surfaced early.

#### Acceptance Criteria

1. WHEN a push or pull-request event triggers the CI_Pipeline, THE Security_Scanner SHALL run `npm ci` in the `frontend/` directory to install dependencies using Node.js 20.
2. WHEN `npm ci` completes successfully, THE Security_Scanner SHALL run `npm audit --audit-level=high` against the installed `node_modules` in the `frontend/` directory.
3. WHEN `npm audit` reports one or more vulnerabilities at severity `high` or `critical`, THE Security_Scanner SHALL exit with a non-zero status code and cause the CI_Pipeline to fail.
4. WHEN `npm audit` reports only `moderate` or lower severity vulnerabilities, or no vulnerabilities, THE Security_Scanner SHALL exit with a zero status code.
5. THE Security_Scanner SHALL run `npm ci` itself as part of the frontend scanning job, in a job that declares no `needs` dependency on the `frontend-build` job, so that the lock file and installed packages are consistent without relying on `frontend-build`'s `npm ci`.
6. THE Security_Scanner SHALL run the frontend dependency scan in a job that declares no `needs` dependency on the Python dependency scan job, allowing both scan results to be reported in the same workflow run regardless of the other's outcome.

---

### Requirement 4: Container Image Vulnerability Scanning

**User Story:** As a developer, I want the Docker images produced by the project scanned for OS and library CVEs before they are published, so that known vulnerabilities are not shipped to production.

#### Acceptance Criteria

1. WHEN the `compose-smoke` job completes successfully on a push to `master`, THE Security_Scanner SHALL scan each application Docker image defined in `deploy/docker-compose.yml` using Trivy before `docker compose down` is run; the images are already present in the local Docker daemon from `compose-smoke`'s `docker compose build`, so no separate build step is needed.
2. WHEN Trivy detects a vulnerability of severity `CRITICAL` or `HIGH` (OS or library) in any scanned image, THE Security_Scanner SHALL exit with a non-zero status code and cause the CI_Pipeline to fail.
3. WHEN Trivy detects no `CRITICAL` or `HIGH` vulnerabilities in any scanned image, THE Security_Scanner SHALL exit with a zero status code.
4. IF the `compose-smoke` job fails, THEN THE Security_Scanner SHALL skip the image scan and not independently block the workflow; the skipped image scan does NOT mask or hide the `compose-smoke` failure, and the overall CI_Pipeline still fails due to `compose-smoke` failing.
5. THE Security_Scanner SHALL upload the Trivy scan results as a GitHub Actions job summary containing all findings of any severity, so they are visible in the pull request and push workflow UI.

---

### Requirement 5: Docker Image Build and Publish to GHCR

**User Story:** As a developer, I want Docker images built and pushed to GHCR on every successful merge to `master`, so that a versioned, CI-verified artifact is available for deployments and local testing.

#### Acceptance Criteria

1. WHEN a push to `master` triggers the CI_Pipeline and all of `lint`, `test`, `frontend-build`, `slim-boundary`, `compose-smoke`, and Security_Scanner complete successfully, THE Image_Publisher SHALL build and push each application Docker image defined in `deploy/docker-compose.yml` (excluding third-party images) to GHCR under the `ghcr.io/<org>/<repo>/<service>` naming convention.
2. WHEN publishing an image, THE Image_Publisher SHALL tag each image with both the full Git commit SHA and the `latest` tag.
3. WHEN the CI_Pipeline is triggered by a pull-request event (not a push to `master`), THE Image_Publisher SHALL NOT push any images to GHCR.
4. THE Image_Publisher SHALL authenticate to GHCR using the `GITHUB_TOKEN` secret provided automatically by GitHub Actions, without requiring any manually managed registry credentials; IF GHCR authentication using `GITHUB_TOKEN` fails, THEN THE Image_Publisher SHALL immediately exit with a non-zero status code and cause the CI_Pipeline to fail.
5. WHEN an image push to GHCR fails, THE Image_Publisher SHALL exit with a non-zero status code and cause the CI_Pipeline to fail, regardless of how many images have already been pushed successfully in that run; already-pushed images are left in GHCR with no rollback.
6. THE Image_Publisher SHALL configure Docker layer caching (via GitHub Actions cache or registry cache) so that unchanged layers are not rebuilt on successive pushes to `master`.
7. WHEN any prerequisite job (`lint`, `test`, `frontend-build`, `slim-boundary`, `compose-smoke`, or Security_Scanner) fails, THE Image_Publisher SHALL NOT run; Security_Scanner success does NOT override failures in any other required job.
8. IF the Security_Scanner itself fails (not just produces vulnerability findings), THEN THE Image_Publisher SHALL also NOT run.
