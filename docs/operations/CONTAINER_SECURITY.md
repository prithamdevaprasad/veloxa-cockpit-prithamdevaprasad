# Container image security

**Checked:** 17 Nov 2026, image built locally from this branch (`veloxa-sprints:local`). Nothing was pushed.

## Results

| Check | Result |
|---|---|
| Runs as non-root | Yes (uid 10001, user `veloxa`) |
| No data, dumps or secrets baked in | No `.env`, `*.dump`, `*.xlsx`, `*.pdf`, `*.eml` or `*.docx` outside site-packages. Release packs are mounted read-only at run time |
| Python packages match the audited lockfile | Yes: `pip freeze` in the image equals `requirements.lock`. The only exception is `colorama`, which is Windows-only |
| Python and npm known-vulnerability audit (`scripts/dependency_audit.py`) | Python clean (pip-audit 2.10.1). npm: one unpatched development-time advisory since 18 Nov (`docs/DEPENDENCIES.md`); not in the image, which has no Node packages |
| Base image | `python:3.12-slim` on Debian 13.7, 87 OS packages. Local digest: `python@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b` |
| OS-package CVE scan | **NOT RUN.** No scanner is available locally: Docker Scout is installed but not signed in, and Trivy/Grype are not installed |

## Findings and recommendations

1. **Pin the base image by digest.** It is currently pinned by tag only. Pinning by digest makes builds reproducible. It needs a planned update cadence for security releases.
2. **Separate the runtime image from the test image.** The image installs the `dev` extras and copies `tests/` so that CI can run tests in it. A production image should install the runtime dependencies only. A two-stage build is suggested.
3. **Choose an image scanner (owner decision).** Options:
   - Docker Scout with an organisation account;
   - Trivy or Grype, run as a pinned container image or binary verified by digest.

   Recent supply-chain incidents affected scanner GitHub Actions and images, so pin by digest and avoid mutable tags. Once chosen, add it as a CI job that fails on fixable high and critical findings.
4. **`.dockerignore` added** (17 Nov 2026) as defence in depth. The Dockerfile already copies named paths only (`src`, `config`, `tests`, and the project files).
