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


## Implementation log — 2026-11-13
- Added a complete local ZIP backup containing a consistent SQLite snapshot (via SQLite backup API), a JSON export, and all regular files under the local references directory.
- Added a backup manifest with SHA-256 hashes for every payload file; the archive is reopened and every member is integrity-checked before it is published.
- Added a streaming hash verification path so large reference collections do not need to be loaded into memory as a single byte array.
- Added `GET /api/backup/full` and a Settings action to download the complete ZIP backup; the existing JSON-only export remains available.
- The update-apply route now creates a verified full backup before staging the program update. If backup creation fails, update staging is not started.
- Added automated coverage for archive members, hashes, SQLite integrity, reference preservation, API/UI wiring, and pre-update snapshot ordering.
- GitHub Actions run 43344908636 completed Python compilation, frontend JavaScript syntax, manifest hash validation, all repository regression tests, and Windows PowerShell parser validation successfully. The separate release source/channel gate remains blocked because the branch runtime still declares APP_VERSION 0.5.2 and GITHUB_BRANCH main; no production release or main-branch change was made.


## Candidate integration status — 2026-11-13
- The staging branch now identifies itself as `APP_VERSION=0.6.5` and targets only `release/v0.6.5-stabilization`; it does not target `main`.
- The release source/channel gate now passes. This validates candidate version/channel consistency only; it is not a production release or GitHub Release tag.
- Integrated V0.6.4 frontend behavior with the hardened retrieval/composition UI: persistent folder auto-monitoring, browser heartbeat shutdown, and 1–8 image grouping/analysis.
- Full ZIP backup and automatic pre-update snapshot are integrated. The manifest includes the required runtime files and SHA-256 values.
- Latest GitHub Actions run: 54 regression tests passed; Python compile, inline JavaScript syntax, release manifest hashes, Windows PowerShell parser, and release source/channel gate all passed.
- Remaining release gates are native Windows acceptance (launch, close, restart, automatic scan, full backup download, update, failure rollback, and user-data/reference preservation) plus final security/functional review. Keep `main` unchanged until these gates pass.


## Implementation log — 2026-11-13 (source provenance)
- Approved AI history imports now persist a structured `provenance` record on the case itself: import type, analysis ID, model, import timestamp, original prompt path and SHA-256, and every original media path/type/SHA-256.
- The case detail view now exposes this provenance, so a reusable successful case can be traced back to the exact source files and analysis run instead of only retaining copied reference images.
- Added storage regression assertions for the original paths, hashes, model, analysis ID, and image source files; added integration guards for the provenance UI.
- Refreshed the V0.6.5 update manifest hashes for all runtime files after integration.
- GitHub Actions run 43348983368 completed successfully: Python compilation, inline JavaScript syntax, release manifest SHA-256 validation, complete regression suite, Windows PowerShell parser validation, and release source/channel gate all passed.
- This is a staging-branch implementation only. No changes were made to `main`; Windows-native runtime acceptance is still a separate release gate.


## Implementation log — 2026-11-13 (batch asset management)
- Added multi-select controls to the visual case library, including select-all-visible and clear-selection actions.
- Added batch status updates (verified / pending / failed) and batch tag addition for selected cases.
- Added explicit-confirmation batch deletion. The existing storage layer records deleted assets in revision history before removal, so deleted cases remain recoverable through the version restore workflow.
- Hardened the shared save helper to surface HTTP/application save failures. Batch status and tag operations restore the in-memory dataset if persistence fails; deletion also restores the previous dataset on failure.
- Added integration guards for the batch controls and rollback behavior. The visual selection checkbox is anchored to each case card.
- GitHub Actions run 43352327949: JavaScript syntax, manifest SHA-256 validation, 56 regression tests, Windows PowerShell parser validation, and release source/channel gate are being checked on the staging branch. Production `main` remains untouched.


## Implementation log — 2026-11-13 (batch prompt module management)
- Extended the batch workflow to reusable prompt modules: select all visible modules, clear selection, add a shared tag, and delete selected modules after explicit confirmation.
- Batch tag and delete operations restore the prior in-memory module list if persistence fails; deletions continue to use the storage layer's per-asset revision retention.
- Added integration guards for module selection, bulk tagging, and deletion. Runtime manifest SHA-256 was refreshed after the UI changes.
- GitHub Actions run 43352556214: frontend JavaScript syntax, release manifest hash validation, 56 regression tests, Windows PowerShell parser, and source/channel gate are passing or completing on the isolated staging branch. No production files were changed.


## Implementation log — 2026-11-13 (SKU retrieval and transactional single-asset actions)
- Added a dedicated optional product SKU/style-number field to the case editor and persisted it with the case.
- Added SKU/style-number/product-code fields to the visual library's local keyword search, case-card metadata, and smart-search result metadata. This closes the prior gap where the matcher supported exact SKU lookup but the normal editor did not expose a field to populate it.
- Single-case and single-module edits now roll back the in-memory list if saving fails; single-asset deletion is awaited and similarly restores the prior list on persistence failure.
- Added integration assertions for SKU capture/indexing and transactional save/delete behavior.
- GitHub Actions run 43352920143 completed successfully: frontend JavaScript syntax, all release manifest SHA-256 values, 56 regression tests, Windows PowerShell parser validation, and release source/channel gate passed. The branch remains isolated; Windows GUI/runtime acceptance is still pending.


## Implementation log — 2026-11-13 (smart-search rendering defect and SKU detail visibility)
- A focused frontend review found that the smart case-search result template had escaped its JavaScript template interpolations. The source passed syntax checks, but the result cards would display literal template expressions instead of actual case data. Corrected all eight escaped interpolations in the smart-search rendering function.
- Added SKU/style-number/product-code to the case detail metadata so the identifier is visible when opening a historical result.
- Added regression guards to ensure smart-search templates contain no escaped interpolation markers and SKU is present in case details.
- GitHub Actions run 43354537053 completed successfully: frontend JavaScript syntax, runtime manifest SHA-256 validation, all 56 regression tests, Windows PowerShell parser validation, and release source/channel gate passed.
- This is an isolated staging-branch fix; no production release or main-branch update was made.
