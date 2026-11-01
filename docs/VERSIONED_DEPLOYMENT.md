# Versioned application-only deployment

This is the upgrade procedure for the existing Ubuntu 24.04 / Python 3.12 SERVER
profile, account `megaraid`. It replaces per-release executable helpers for future
compatible app updates. [Fresh installation](../INSTALL.md), schema migrations,
account/security-policy changes, firmware and StorCLI upgrades are separate procedures.

The installed application checkpoint remains [#141 / 27af3da](CURRENT_STATE.md).
Installing this tool or recording an existing installation does not upgrade the app.
The owner requested a maintenance handoff, not an automatic update/monitoring service.

## Commands and trust boundary

`scripts/deploy_release.py` uses only Python's standard library. Mutating commands
require root and a root-controlled copy of the tool, including its ancestor directories.
The tool never invokes StorCLI or grants sudo permissions. Existing controller writes
retain their own application authorization and audit gates.

| Command | Effect |
| --- | --- |
| `bundle` | Unprivileged construction and validation of a new immutable offline bundle |
| `plan` | Verify the independently approved manifest hash, all input hashes and source/wheel agreement; no service changes |
| `start` | Queue a detached systemd worker; report its unit and durable receipt path |
| `apply` | Internal root worker; use start for normal operation so SSH loss does not kill it |
| `status` | Read the persisted receipt plus live service/health; distinguish historical receipt from current state |
| `adopt` | Verify an already-running legacy installation and persist its evidence, without restart or database write |
| `rollback` | Explicit guarded application rollback using the private durable context, without needing staged bundle files |

Install the reviewed, hash-verified tool as `/usr/local/sbin/megaraid-dashboard-deploy.py`,
root:root, mode 0755. Do not run a worker from a service- or user-writable tool path.
Do not extend passwordless sudo to this program. The operator's existing sudo password
authorization remains necessary.

The manifest SHA-256 is an approval anchor, not a digital signature. Capture it from
the reviewed preparation and put that exact value in the activation command. Never
recalculate and accept a new digest from mutable staging just to make a refusal pass.
Bundle release/baseline identities must be full commit SHAs. No arbitrary executable,
service, path, account, schema or privilege knobs are accepted in the manifest.

## Prepare a release

1. Use a clean checkout of the exact reviewed/merged commit. Required local checks,
   current-head review and all GitHub CI jobs must pass before activation. Compare the
   merged tree with the reviewed tree when a squash changes the commit SHA.
2. Build on Ubuntu 24.04 x86-64 / Python 3.12, matching SERVER. Network access is allowed
   only during preparation; root activation installs from the verified wheelhouse offline.
3. Use [the pinned runtime lock](../deploy/runtime-lock.txt), copied byte-for-byte from the
   already verified production dependency set. This batch upgrades no dependencies.
   Any later dependency change requires its own reviewed compatibility/test evidence.

Example preparation, with operator-selected directories and the actual approved SHAs:

```bash
candidate_sha="$(git rev-parse HEAD)"
baseline_sha='27af3da95318ce3f902eaa9bf6a86dad89710da2'
build_dir="$(mktemp -d)"
mkdir "$build_dir/source" "$build_dir/wheels"
git archive --format=tar --output="$build_dir/source.tar" "$candidate_sha"
tar -xf "$build_dir/source.tar" -C "$build_dir/source"
python3 -m venv "$build_dir/build-env"
"$build_dir/build-env/bin/python" -m pip install 'build>=1.2,<2' 'hatchling>=1.24,<2'
"$build_dir/build-env/bin/python" -m pip download --only-binary=:all: --no-deps \
  -r deploy/runtime-lock.txt --dest "$build_dir/wheels"
"$build_dir/build-env/bin/python" -m build --wheel --no-isolation \
  --outdir "$build_dir/wheels" "$build_dir/source"
python3 scripts/deploy_release.py bundle \
  --source "$build_dir/source.tar" --lock deploy/runtime-lock.txt \
  --wheelhouse "$build_dir/wheels" --output "$build_dir/bundle" \
  --commit "$candidate_sha" --baseline "$baseline_sha"
```

Save the printed `manifest_sha256` in the private release receipt. Transfer the bundle
to a private SERVER staging directory. `source.tar`, `runtime-lock.txt`, every wheel and
`release.json` are checked before activation. The application wheel must contain exactly
the source package plus the project's force-included Alembic files. Unsafe/duplicate tar
members, links, unexpected wheel members and lockfile URLs/options are refused.

```bash
bundle_dir='/home/alexey/APPROVED_RELEASE_BUNDLE'
approved_manifest_sha256='REPLACE_WITH_REVIEWED_64_HEX_DIGEST'
python3 /usr/local/sbin/megaraid-dashboard-deploy.py plan \
  --bundle "$bundle_dir" --manifest-sha256 "$approved_manifest_sha256"
sudo /usr/bin/python3 -I /usr/local/sbin/megaraid-dashboard-deploy.py start \
  --bundle "$bundle_dir" --manifest-sha256 "$approved_manifest_sha256"
```

Placeholders intentionally fail validation. Use the actual private staged directory,
approved digest and installed root-owned tool. `queued` means the worker was dispatched,
not that installation or verification finished. The systemd unit survives SSH disconnect.

## Status, repeat requests and evidence

```bash
python3 /usr/local/sbin/megaraid-dashboard-deploy.py status --commit "$candidate_sha"
```

Public, root-owned receipts live at
`/var/lib/megaraid-dashboard-deploy/FULL_COMMIT_SHA.json`, not `/run`. Writes use fsync
and atomic replacement. Private logs, configuration/SQLite backups and recovery context
live in a mode-0700 `/var/backups/megaraid-release-*` directory. Do not publish their
contents. The separate receipt store is not owned or writable by the web service.

| State/result | Meaning and next action |
| --- | --- |
| `queued` / `running` | Work is pending/in progress; do not launch a competing operation |
| `preflight` / `refused` | Durable preflight phase/refusal; no_changes marks a refusal before application changes. Correct the cause and explicitly repeat to recheck every guard |
| `backing_up`, `installing_offline`, `activating`, `active_waiting_for_collector` | Persisted worker phase; wait for a terminal result |
| `verified` | Managed activation passed health/TLS, configuration/policy preservation and a newer normal sample from the new process |
| `already_installed` | The same verified healthy release and protected evidence match; no restart/reinstallation, original receipt retained |
| `verified_external` | Explicit read-only adoption of a healthy legacy deployment; no new backup or managed rollback context is claimed |
| `not_current_or_unhealthy` | A success receipt exists, but the queried release is not currently active/healthy; inspect live facts |
| `interrupted_needs_operator` | Nonterminal receipt and no active worker; inspect the private context/log before recovery |
| `not_activated` | Failure before restart; existing application retained, partial candidate may remain for inspection |
| `rolled_back` / `already_rolled_back` | Managed application rollback completed / repeat recovery is a no-op |
| `rollback_needs_operator` | Safe automatic recovery could not be proved; do not bypass guards or overwrite another actor's changes |
| `not_recorded` | No managed/adopted receipt; inspect the legacy deployment evidence rather than inventing success |

An `already_installed` response rechecks current release identity, health, effective settings
and protected configuration. A changed/unhealthy release is refused, not silently restarted.
Failed/partial attempts are not overwritten by blind retry. An unrelated existing target
directory or unknown higher-priority/foreign override is refused before activation.

## Adoption of the existing application

For an already-running legacy release, construct/verify a bundle from that exact existing
release and its originally approved baseline. Use `adopt`, not `start`, with its reviewed
manifest hash. Adoption checks release/source identity, the effective pinned-policy profile,
schema, current process collection evidence and a recent valid saved sample. It persists
`verified_external` without restarting a service, modifying the live database or executing
a controller command. Repeating it returns already_installed when evidence still matches.

Adoption does not manufacture an application backup or migrate a legacy rollback procedure.
Keep the original private legacy backup/status receipt. `rollback` explicitly refuses an
external attestation; use the original reviewed recovery record if that becomes necessary.

## Preservation and rollback

The existing legacy configuration directory is service-owned. The tool preserves its
observed ownership/modes, rejects symlinks/hardlinks/races and compares its identity/hash;
it never "repairs" its permissions to fit a root-only template. Root-controlled policy,
binary, sudoers, unit, previous env files and drop-ins are hashed and preserved.

Only a new `GIT_SHA` env and the recognized application-only `zzzz-release.conf` override
change. An existing override must match this tool's known application-only form; unrelated
directives are not discarded. Its previous bytes are saved for rollback. Account/group,
the existing privilege configuration, settings, approved commands and replacement topology
are checked. Changed migrations/Alembic configuration are refused before service restart.

After restart, allow the normal 300-second collector cadence. The worker requires a new
process, newer saved sample and that process's collection completion; it retains Learn
Cycle and compact Patrol/foreign validation. It does not force a controller probe or job.
Finish with relevant authenticated GET-only UI checks.

On a normal activation failure, automatic rollback restores only the known application
override and removes its own new version env, then verifies the previous release. The old
release directory is retained. Newer DB snapshots, audit rows, deliveries and cooldowns are
never restored/deleted. A copied SQLite backup is recovery material, not the routine rollback.

For an interrupted managed operation, after inspecting its private evidence and confirming
the task authorizes recovery:

```bash
sudo /usr/bin/python3 -I /usr/local/sbin/megaraid-dashboard-deploy.py rollback \
  --commit "$candidate_sha"
```

The durable recovery context works without the original staged wheelhouse. It refuses a
different active release, changed policy/configuration or externally edited override rather
than overwriting unrelated work. A partial candidate directory remains for deliberate
inspection; do not automatically delete it or disable guards to retry.

## Verification boundary

`tests/test_release_deploy.py` uses real temporary files/SQLite and fake OS/service boundaries.
It covers detached worker command construction, concurrent/repeated requests, durable
recovery, unchanged DB/audit history, prior override restoration, archive/hash/ownership
refusals and read-only adoption. Production host/service faults and RAID writes are not
induced to test rollback. Strict mypy includes the tool in local and GitHub CI.
