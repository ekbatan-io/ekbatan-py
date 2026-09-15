# Releasing ekbatan-py

Two distributions go to PyPI, and the order matters because the second depends on the first:

1. **`ekbatan-py-db-scheduler`** - the vendored port of `kagkarlsson/db-scheduler`, built from
   `db-scheduler/`. Java depends on the upstream artifact and pins it in `gradle.properties`;
   there is no upstream on PyPI, so this repository publishes it and `ekbatan` pins it the same
   way.
2. **`ekbatan`** - the framework, built from the root.

There are **no credentials anywhere in this pipeline.** PyPI's trusted publishing exchanges a
GitHub Actions OIDC token for a short-lived upload token at the moment of the upload. Nothing is
stored in a repository secret, nothing expires, nothing has to be rotated, and there is no
signing key to lose - which is why this document is a third the length of the Java one and has
no counterpart to its "Recovering from a lost GPG keyring" section. PyPI has verified the
uploader's identity since 2023 by *who is asking*, not by *what they hold*.

`uv` runs every step. `uv build` produces the artifacts and `uv publish` uploads them, including
the PEP 740 attestations - so the Java pipeline's GPG signatures have a counterpart after all,
it just needs no key.

---

## Phase 0 - one-time setup

Done once. Never repeated.

### 1. A PyPI account with 2FA

Sign up at <https://pypi.org>. PyPI has required two-factor authentication for every uploader
since 2024; set it up now rather than discovering it at the release.

### 2. Register a pending publisher for each distribution

Neither name has been published yet, so there is no project to configure - which is what a
**pending publisher** is for: it claims the name and the trust relationship in one step, and
becomes an ordinary publisher on the first successful upload.

Go to <https://pypi.org/manage/account/publishing/> and add **two**, identical except for the
project name:

| field | `ekbatan` | `ekbatan-py-db-scheduler` |
| --- | --- | --- |
| PyPI Project Name | `ekbatan` | `ekbatan-py-db-scheduler` |
| Owner | `ekbatan-io` | `ekbatan-io` |
| Repository name | `ekbatan-py` | `ekbatan-py` |
| Workflow name | `release.yml` | `release.yml` |
| Environment name | `pypi` | `pypi` |

All five must match exactly what the workflow presents or the upload is rejected. The workflow
name is the **filename**, not the `name:` inside it.

### 3. The same two on TestPyPI

<https://test.pypi.org/manage/account/publishing/>, same five fields but environment `testpypi`.
TestPyPI is a separate service with separate accounts; registering there is what makes a real
end-to-end rehearsal possible without burning a version number on the real index.

### 4. Two GitHub environments

<https://github.com/ekbatan-io/ekbatan-py/settings/environments> - create `pypi` and `testpypi`.

An environment is not decoration: it is the second half of the trust relationship. PyPI checks
that the OIDC token names this environment, so a workflow that has not been through it cannot
publish even if it runs in the right repository. Adding a required reviewer to `pypi` gives you
Java's stage-and-confirm pause - a human clicks before anything leaves the runner. Recommended,
because the PyPI step is the one that cannot be undone.

That is the whole of Phase 0. No keys, no tokens, no keyserver, no namespace verification.

---

## Phase 1 - per-release

### Bump both versions, together

The two distributions are cut as one, exactly as Gradle gives all 16 Java modules one version
from `gradle.properties`. Three places, and they must agree:

```
pyproject.toml                    version = "0.X.Y"
db-scheduler/pyproject.toml       version = "0.X.Y"
pyproject.toml                    dependencies = [..., "ekbatan-py-db-scheduler~=0.X.Y"]
```

The third is the pin, and it is the one that is forgotten. The release workflow checks all three
against the tag before it builds anything, which is the counterpart to Java's "Verify
gradle.properties version matches tag" step.

**While the version carries a pre-release suffix the pin must carry it too.** `~=0.1.0` excludes
pre-releases, so against a `0.1.0.dev0` db-scheduler it resolves to nothing - *"your requirements
are unsatisfiable"* - and `~=0.1.0.dev0` is what works. Nothing in this repository would tell
you: `[tool.uv.sources]` replaces that requirement with a path, so `uv sync` and the whole test
suite are blind to it, and the first person to find out is a user running `pip install ekbatan`.
The `import from the wheel` step in the release workflow is there to be that person first; it
installs the built wheel from an index that contains only these two files, so a pin that cannot
resolve fails the release instead of the user.

```bash
git add pyproject.toml db-scheduler/pyproject.toml uv.lock
git commit -m "EKB-XXXX release 0.X.Y"
git push origin main
```

### Rehearse on TestPyPI

```bash
gh workflow run release.yml -f target=testpypi -f ref=main
```

This builds and uploads both distributions to TestPyPI, then installs `ekbatan` from it into a
clean environment and imports it. It is the nearest thing to Java's stage-and-confirm, and it is
worth doing: a version burned on TestPyPI costs nothing.

### Tag and push

```bash
git tag v0.X.Y
git push origin v0.X.Y
```

The tag push fires `.github/workflows/release.yml`, which:

```
verify        the tag, both pyproject versions and the pin all say 0.X.Y
build         uv build, at the root and in db-scheduler/
smoke         install the built wheel into a clean venv and import it
publish       uv publish, db-scheduler first, then ekbatan
release       gh release create, with both sdists and wheels attached
```

`publish` runs in the `pypi` environment, so a required reviewer stops it there.

### Verify

```bash
# Give the CDN a minute, then:
uv run --no-project --with ekbatan==0.X.Y python -c "import ekbatan; print(ekbatan.__file__)"
```

<https://pypi.org/project/ekbatan/0.X.Y/> shows the page a user will read. Check that the README
rendered, the classifiers are right, and the licence shows as Apache-2.0 - all three come from
`pyproject.toml` and all three are silent when wrong.

---

## Local dry runs

Before any of the above, from a clean tree:

```bash
make clean && make lint && make test-all     # what CI would run
uv build && (cd db-scheduler && uv build --out-dir ../dist)

# Validates metadata and filenames, uploads nothing, exits 0.
uv publish --dry-run --trusted-publishing never --token dummy dist/*

# What a user gets. Not the same thing as the tests passing: this is the one check that
# catches a package left out of the wheel, or a dependency that only the dev group provided.
uv run --no-project --with dist/ekbatan-0.X.Y-py3-none-any.whl python -c "
from ekbatan import Model, Entity, ActionExecutor; print('ok')"
```

`--trusted-publishing never --token dummy` is not a trick: without it `uv publish --dry-run`
tries to fetch an OIDC token, fails with *"No OIDC token discovered: are you in a supported
trusted publishing environment?"* and exits non-zero. That failure is expected off a runner and
says nothing about the artifacts. Java has the same wrinkle - `jreleaserConfig` demands
credentials to validate config it will not upload.

---

## What is recoverable, and the one thing that is not

| step | recoverable? |
| --- | --- |
| a `.devN` or `.rcN` release on PyPI | yes in practice - pre-releases are not installed by default, so a bad one is invisible to `pip install ekbatan` |
| anything on TestPyPI | yes - it is periodically pruned and nobody depends on it |
| a tag pushed to git | yes - `git push --delete origin v0.X.Y` |
| a GitHub Release | yes - edit or delete in the UI |
| **a file uploaded to PyPI** | **no. Read the next paragraph** |

**PyPI is stricter than Maven Central here, in a way that surprises people.** You *can* delete a
release from PyPI - but the filename is burned forever. `ekbatan-0.3.0-py3-none-any.whl` can be
uploaded exactly once in the history of the project; after deleting it, re-uploading the same
filename is rejected, and 0.3.0 is simply gone as a version number. Maven Central will not let
you delete at all, but at least a dropped *staging* deployment leaves the version free. PyPI has
no staging.

So: a mistake noticed after upload is fixed by shipping `0.3.1`, never by deleting and retrying.
This is why the TestPyPI rehearsal and the required reviewer on the `pypi` environment are worth
the ceremony.

---

## Troubleshooting

| symptom | cause | fix |
| --- | --- | --- |
| `invalid-publisher: valid token, but no corresponding publisher` | one of the five fields on PyPI does not match the workflow | compare all five. Usually the environment name, or `release.yml` given as the `name:` rather than the filename |
| `No OIDC token discovered` in Actions | the job is missing `permissions: id-token: write` | it is per-job, and a job-level `permissions:` block replaces the workflow-level one rather than adding to it |
| `File already exists` | this version was published before, or a re-run is uploading the same files | if it is a re-run, `--check-url` makes the upload idempotent - the workflow passes it. If it is a real collision, bump the version; the filename cannot be reused |
| the README renders as raw text on PyPI | a `readme` whose content type PyPI could not infer | `readme = "README.md"` in `pyproject.toml` is enough for hatchling; check the built METADATA for `Description-Content-Type: text/markdown` |
| `ekbatan` installs but `ekbatan-py-db-scheduler` cannot be found | the two were published out of order, or only one was | publish db-scheduler first. The workflow does; a manual upload is where this happens |
| the pin resolves to an older db-scheduler | the `~=0.X.Y` pin was not bumped with the version | fix the pin, ship a patch. The workflow's verify step catches this before the build, so it only bites a manual release |

---

## Why there is no `ekbatan-debezium-smt` here

Java publishes two shadow jars to GitHub Releases as drop-in Kafka Connect plugins. There is no
Python counterpart and there will not be: the transform runs inside Kafka Connect, in a JVM,
whichever language wrote the outbox row. A Python service points the *Java* SMT at its database.
What this port owes is the row format, which is `ekbatan.schema` and is pinned by the shared
conformance scenarios. `docs/SHORTCOMINGS.md` records it as deliberate rather than missing.
