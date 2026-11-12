# Prompt Atelier V1.0 Release Plan

This branch is an isolated stabilization lane. It is not the production update channel until Windows acceptance and release verification are complete.

## Release policy
- Never publish a candidate manifest until every listed file exists on this branch and its SHA-256 matches the manifest.
- Never point a candidate client at `main` while the candidate is maintained on another branch.
- Never merge to `main` before Windows launch, close, restart, update, health-check, rollback, and data-preservation acceptance.
- Keep user databases, reference images, credentials, and local configuration outside the program update allowlist.
- Preserve a pre-update snapshot and verify the installed version through `/api/version` before reporting success.

## Milestones
1. M0: inventory current source, data paths, endpoints, updater, and startup lifecycle.
2. M1: interaction states, truthful save/delete/import results, request de-duplication, timeout handling, and diagnostics.
3. M2: backup/restore, updater integrity, transactional rollback, and process lifecycle.
4. M3: asset taxonomy, case provenance, batch operations, and version history.
5. M4: explainable multi-dimensional matching with hard constraints and configurable thresholds.
6. M5: prompt module composition, conflict detection, reference priority, bilingual output, and revisions.
7. M6: analytics, visual similarity retrieval, model-specific guidance, and extensibility.

## Mandatory gates
- Static and syntax checks.
- Automated unit and API regression tests.
- Manifest/file SHA-256 consistency.
- Clean-install and in-place-update tests in isolated Windows environments.
- Windows start/stop/restart and port lifecycle tests.
- Update failure and rollback tests.
- Database and reference-image preservation tests.
- No unresolved critical/high severity regressions.

## Current status
The V0.6.5 candidate has passed its included 20-test hardening suite and 24-item static quality check in the available non-Windows environment. Windows native acceptance remains outstanding. The branch must not be treated as a production release until the gates above are met.
