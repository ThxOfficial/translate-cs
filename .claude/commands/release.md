---
description: Release a new version to GitHub
argument-hint: <semver, e.g. 0.3.0>
---

If `$1` is empty, stop and ask the user for the version. Do not proceed with an unset
version: `git tag v$1` would create and push a tag named literally `v`, and the git tag
is the only version anchor in this repo.

`$1` is bare semver (e.g. `0.3.0`). The `v` prefix is applied to the git tag.

Release version `$1` by running these steps in order. Stop and report immediately if any step fails — do not attempt to recover automatically.

1. Push `main`:

   ```bash
   git push origin main
   ```

2. Tag the release:

   ```bash
   git tag v$1 && git push --tags
   ```

Before tagging, confirm `main` was pushed successfully and that the version tag does not already exist. Do not force-overwrite a tag (`git tag -f`) without explicit user approval.

When both steps succeed, report the released version and the outcome of each step.
