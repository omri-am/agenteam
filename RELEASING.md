# Releasing `agensuite`

Publishing is automated via `.github/workflows/publish.yml`. It triggers
**only on a pushed `v*` git tag** — merging PRs never publishes. The tool
uses PyPI **Trusted Publishing** (OIDC), so there are no API tokens stored
anywhere.

> Note: the PyPI distribution is `agensuite`, but the GitHub **repo** is
> still `omri-am/agenteam`. The `agenteam` values below are repo
> coordinates, not the package name.

## One-time setup (do this once, on pypi.org)

Trusted Publishing must be registered *before* the package exists on PyPI —
PyPI calls this a "pending publisher."

1. Create an account at https://pypi.org and enable 2FA.
2. Go to **Your account → Publishing → Add a pending publisher** and enter:
   - **PyPI project name:** `agensuite`
   - **Owner:** `omri-am`
   - **Repository name:** `agenteam`
   - **Workflow filename:** `publish.yml`
   - **Environment name:** `pypi`
3. (Optional but recommended) In the GitHub repo: **Settings → Environments
   → New environment → `pypi`**. Add a required reviewer if you want a
   manual "approve before publish" gate, or restrict which branches/tags can
   deploy.

That's it — no secrets to copy into GitHub.

## Claiming the name now (recommended)

The current version is `0.1.0a0` (an alpha pre-release). Publishing it locks
the `agensuite` name without exposing a "stable" release — `pip install
agensuite` skips pre-releases unless the user passes `--pre`.

```bash
git tag v0.1.0a0
git push origin v0.1.0a0
```

The workflow builds, runs tests, verifies the tag matches the version, and
publishes. Watch it under the repo's **Actions** tab.

## Cutting a real release later

1. Bump the version in `pyproject.toml` (e.g. `0.1.0a0` → `0.1.0`).
   Follow SemVer: PATCH = fixes, MINOR = compatible features, MAJOR =
   breaking. Stay on `0.x` while the API is unstable.
2. Commit it: `git commit -am "release: 0.1.0"`.
3. Tag **with a matching `v` prefix** and push:
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

The tag-vs-`pyproject` guard fails the build if the two disagree, so a
forgotten version bump can't reach PyPI (where versions are immutable and
cannot be re-uploaded).

After the first stable release, flip the "Coming soon" note in `README.md`
to the published `uv tool install agensuite` form.

## Testing the pipeline first (optional)

To rehearse without touching real PyPI, register the same pending publisher
on **TestPyPI** (https://test.pypi.org) and temporarily add
`repository-url: https://test.pypi.org/legacy/` to the publish step. Remove
it before the real release.

## Building locally (sanity check, no upload)

```bash
pip install build twine
python -m build          # writes dist/*.whl and dist/*.tar.gz
twine check dist/*       # validates metadata
```
