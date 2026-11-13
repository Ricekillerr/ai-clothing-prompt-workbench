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

## Current status — refreshed 2026-11-13

- Development remains isolated on `release/v0.6.5-stabilization`; `main` and the production update channel have not been changed.
- Latest GitHub Actions run 43362277749 passed 87 regression tests, Python compilation, frontend JavaScript syntax, all 8 update-manifest SHA-256 checks, four Windows Server 2025 runtime smoke tests, PowerShell parser validation, and release source/channel gate.
- Implemented in this candidate: local full backup and pre-update snapshot; source provenance; folder auto-scan and human-reviewed AI ingestion; structured outfit decomposition and garment-level retrieval; reference-image role/priority metadata with manual correction; local perceptual-hash image shortlist; reference-aware prompt composition; bilingual prompt storage/output; revision comparison/restore; retrieval feedback analytics; serialized saves, request timeouts and privacy-safe diagnostics.
- Important limitations still tracked: browser perceptual hashing is a coarse visual shortlist, not semantic same-garment recognition; English versions are stored/reused but not automatically translated; reference numbering must be manually mapped to the original prompt's @image identifiers.
- Remaining V1.0 work: complete model-specific guidance and evaluation, broader visual retrieval validation, security/functional audit, clean-install and in-place-update verification, and native Windows launch/close/restart/backup/update/rollback/data-preservation acceptance.
- This candidate is not a production release. No Windows GUI acceptance is claimed, and no merge to `main` or public release should occur until every mandatory gate passes.


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


## Implementation log — 2026-11-13 (exact SKU retrieval regression coverage)
- Added end-to-end ranking coverage proving an exact SKU query returns the verified case at 100%, not only a raw scoring result.
- Added compatibility tests for legacy style_number and product_code identifiers so older case records remain searchable without migration.
- GitHub Actions run 43354619523 completed successfully: all 58 regression tests, frontend syntax, manifest SHA-256 validation, Windows PowerShell parser validation, and release source/channel gate passed.


## Implementation log — 2026-11-13 (structured garment taxonomy and synonym-aware retrieval)
- Expanded case creation/editing with structured fields for silhouette, fit, fabric composition, surface texture, construction details, craftsmanship, and generation constraints. The case detail view now displays these attributes; existing candidate matching already reads the corresponding fields, so the new metadata is searchable without moving it into free-form prompt text.
- Expanded garment vocabulary for common Chinese apparel terms, including garment lengths/cuts, fabric names, stitching terms, and Seedance model references.
- Added canonical normalization for common material/color aliases (for example, 锦纶/尼龙, 涤纶/聚酯, 真丝/丝绸, 藏青/蓝色) before tokenization, avoiding partial matches from retaining both alias and canonical tokens.
- Added structured-field and synonym regression tests. Initial tests exposed missing field indexing and partial alias coverage; corrected the matcher to include fit/silhouette in detail attributes and classify double-needle stitching phrases. No failing test was waived or deleted.
- GitHub Actions run 43355300227 completed successfully: all 61 regression tests, Python compilation, frontend JavaScript syntax, runtime manifest SHA-256 checks, Windows PowerShell parser validation, and release source/channel gate passed.
- Candidate remains on the isolated stabilization branch; no production release, main-branch merge, or Windows GUI acceptance is claimed.


## Implementation log — 2026-11-13 (per-image role classification and human review)
- AI analysis now requests one ordered role classification for every image input: overall, front, back, detail, fabric, label, scene, other, or unknown. Videos are explicitly excluded from image-role indexing because this version does not extract video frames.
- Added deterministic normalization: missing, malformed, duplicate, out-of-range, or unsupported role labels cannot shift image order or silently become trusted classifications; they fall back to unknown or retain the first valid index entry for human review.
- AI review queue displays the proposed role labels before import. Confirmed cases preserve per-image role, Chinese label, observation, and source path alongside the existing copied references and SHA-256 provenance. Case details display the saved classifications and observations.
- The workflow remains human-confirmed: AI role labels are suggestions, not automatic truth. No case is marked as verified-successful by this classification step.
- Added tests for ordered role-to-image mapping, invalid/missing/duplicate role fallback, and role metadata persistence through approved import. GitHub Actions run 43355844148 passed all 64 regression tests, Python compilation, frontend syntax, runtime manifest hashes, Windows PowerShell parser validation, and release source/channel gate.
- Candidate remains isolated on `release/v0.6.5-stabilization`; Windows GUI/runtime acceptance remains outstanding.


## Implementation log — 2026-11-13 (structured outfit decomposition and garment-level retrieval)
- AI history analysis now extracts up to 12 individual outfit items with bounded fields for category, subcategory, name, color, material, silhouette, fit, construction details, visible evidence, and a review score. It also extracts explicit pairwise outfit relationships only when the prompt or images support them; unsupported or out-of-range relations are discarded.
- Item normalization skips malformed entries while retaining up to 12 valid garments, bounds text lengths and detail counts, and limits relation count. The initial regression test caught a cap edge case where malformed entries consumed the valid-item limit; the normalizer was corrected rather than weakening the test.
- Approved cases persist the outfit item list and relations. AI review previews the proposed breakdown; saved case details show each item and evidence, explicitly labelling it as AI-identified and requiring human verification.
- Case retrieval now indexes individual outfit item attributes, so a query for one garment (for example, a corduroy trouser) can retrieve its parent complete-outfit case without flattening away the item structure.
- Found and fixed an incremental-update coverage gap: `collector.py` and `case_matcher.py` were imported runtime modules but absent from the update manifest. Both are now included with SHA-256 pins. The CI hash reporter now reads the manifest's actual file list instead of maintaining a separate hard-coded list, reducing future drift.
- Added tests for item normalization, relation validation, persistence, garment-level retrieval, UI integration, and manifest dependency coverage. Regression suite passed 67 tests on GitHub Actions run 43356694190 before the final manifest-coverage guard was added; the updated manifest and new guard are undergoing a fresh full run.
- Candidate remains isolated on `release/v0.6.5-stabilization`. No production release is claimed; native Windows GUI/update/rollback acceptance remains outstanding.


## Implementation log — 2026-11-13 (V1.0 outfit breakdown validation)
- The final manifest now includes all eight files in the actual incremental update payload, including the previously omitted imported modules `collector.py` and `case_matcher.py`. CI's runtime hash reporter derives its file list from the manifest itself, and a regression guard verifies both modules are present and pinned.
- Full GitHub Actions run 43356815996 passed: all 67 regression tests, Python compilation, frontend JavaScript syntax, SHA-256 validation of all 8 update files, Windows PowerShell parser validation, and release source/channel gate.
- This confirms the structured outfit decomposition and garment-level retrieval candidate is internally consistent in CI. It does not replace native Windows GUI acceptance or prove visual-semantic image similarity; those remain separate V1.0 tasks.


## Implementation log — 2026-11-13 (editable outfit structure and local visual shortlist)
- The case editor now supports manual maintenance of structured outfit items and explicit pairwise relationships, using one garment per line with category, name, color, material, silhouette, fit, and details. Item count and relationship endpoints are bounded; editing preserves existing AI evidence metadata for retained entries.
- Added a browser-local perceptual-hash image shortlist in smart search. The query image is processed in the browser and is not uploaded. It compares against up to 500 images attached to verified-success cases and displays nearest visual-structure candidates with a clear limitation notice: this is a coarse near-duplicate/visual-structure aid, not semantic same-garment recognition; different viewpoints and backgrounds can reduce accuracy.
- Added integration guards for manual outfit editing and local-only visual search. Frontend JavaScript syntax was checked by CI; final manifest hash and complete test run are in progress after the last UI update.


## Implementation log — 2026-11-13 (manual outfit editing and visual shortlist validation)
- Manual editing of outfit items/relationships and the browser-local visual shortlist passed the complete GitHub Actions pipeline. Run 43357194030 passed all 67 regression tests, frontend syntax validation, all 8 update-file SHA-256 checks, Windows PowerShell parser validation, and release source/channel gate.
- The image shortlist remains intentionally scoped to perceptual-hash similarity, not semantic recognition. It does not upload the query image, and results must not be represented as an 80% same-product probability.
- No Windows desktop acceptance or release promotion is claimed.


## Implementation log — 2026-11-13 (reference authority and governed attributes)
- AI reference-image analysis now records a priority level (`primary`, `secondary`, `supporting`) and the visual attributes each reference governs. Defaults are role-based: overall/front/back are primary; detail/fabric are secondary; labels, scenes, unknown and other roles are supporting. Valid AI overrides are retained, invalid priorities fall back to the deterministic default.
- Approved cases preserve priority and governed-attribute metadata. The AI review queue and case detail view display the reference authority level, helping later prompt assembly distinguish overall structure references from material/detail references.
- GitHub Actions run 43357442566 passed all 67 regression tests, Python compilation, frontend JavaScript syntax, all 8 update-file hashes, Windows PowerShell parser validation, and release source/channel gate.
- This priority metadata is now available to prompt assembly, but the final V1.0 acceptance still needs to verify that the composer actually emits the right reference precedence and does not silently reorder or override user-selected authority.


## Implementation log — 2026-11-13 (manual reference-role review)
- The case editor now exposes the imported reference-image roles and lets the user correct role type, authority priority, role label, governed attributes, and observation text. Original source-path provenance is retained for each existing image role.
- Role indexes are bounded to the eight-image maximum and, where known, checked against the actual attached image count. Invalid indexes are discarded instead of attaching authority metadata to the wrong file.
- Added integration guards for the editor controls and parser. The last CI attempt reached the expected manifest-hash gate after JavaScript syntax validation; the refreshed hash is being revalidated.


## Implementation log — 2026-11-13 (manual reference authority controls verified)
- Manual reference-role editing, role-index bounds, and the primary/secondary/supporting priority display passed GitHub Actions run 43357598746: 67 regression tests, Python compilation, frontend JavaScript syntax, 8-file update manifest SHA-256 validation, Windows PowerShell parser validation, and release source/channel gate.
- Reference authority is now both machine-suggested and user-correctable; source paths remain attached to the same image index when editing existing records.


## Implementation log — 2026-11-13 (reference-aware prompt composition)
- The prompt composer now has an optional, enabled-by-default structure guide. When the base case contains structured outfit items or reference roles, it appends the garment breakdown, explicit saved layering relations, reference role/priority, governed attributes, and a conflict-precedence rule (primary > secondary > supporting).
- The generated guide clearly states that stored associated-image numbering does not automatically rewrite `@图片` identifiers in the original prompt; the user must verify the mapping. This avoids silently pointing the video model at the wrong reference image.
- Added integration checks for the composer guide and its reference-numbering warning. The code change passed frontend syntax validation; its updated runtime hash and full pipeline are being rechecked.


## Implementation log — 2026-11-13 (prompt revision comparison)
- Added an on-demand endpoint for retrieving one selected asset revision, avoiding loading every full prompt body into the history list.
- The asset history panel now offers side-by-side display of the historical prompt and current prompt before restoration. The comparison is read-only; restore remains a separate explicit action and first snapshots the current state.
- Added storage and integration regression tests for revision retrieval and comparison UI. The runtime manifest was refreshed for the three changed runtime files; full pipeline validation is in progress.


## Implementation log — 2026-11-13 (prompt revision comparison validation)
- GitHub Actions run 43357955322 passed all 67 regression tests, Python compilation, frontend JavaScript syntax, all 8 incremental-update file SHA-256 checks, Windows PowerShell parser validation, and release source/channel gate.
- Prompt revision comparison is now part of the staging candidate. The comparison view is read-only; the separate restore action remains explicit and preserves the pre-restore state.


## Implementation log — 2026-11-13 (retrieval feedback analytics)
- Added a local SQLite feedback ledger for smart-search results. Users can mark a returned case as helpful or not relevant; each record stores the query, case ID, displayed retrieval score, feedback type, and timestamp.
- Added retention bound of 5,000 feedback records, validation against existing case IDs, and an aggregate analytics endpoint with total/helpful/not-relevant counts and top feedback cases.
- Settings now provides a local-only retrieval feedback summary. Feedback is intentionally observational and does not automatically change ranking weights; any future tuning requires a labelled evaluation set and regression tests.
- Added storage and frontend/API integration tests. Runtime manifest hashes have been refreshed; the full pipeline is being rechecked.


## Implementation log — 2026-11-13 (retrieval feedback analytics validated)
- GitHub Actions run 43358221656 passed all 69 regression tests, Python compilation, frontend JavaScript syntax, all 8 update-file SHA-256 checks, Windows PowerShell parser validation, and release source/channel gate.
- The feedback ledger is local-only, bounded to 5,000 records, validates the referenced case, and does not alter retrieval ranking automatically.


## Implementation log — 2026-11-13 (request reliability and diagnostics)
- Browser data saves now serialize through a promise queue so overlapping edits cannot complete out of order and overwrite a newer snapshot. Local data load/save requests have bounded timeouts; generic API calls use a 30-second default and AI analysis gets a longer operation-specific timeout.
- Timeout errors are surfaced to the UI with guidance not to blindly repeat an operation before checking current state. This matters when the server may have committed a write but the response was delayed.
- Added local API error diagnostics that record route and exception class/code only. Request bodies, prompts, URLs, and credential-bearing exception strings are intentionally excluded from the log.
- Added integration guards for save serialization, timeout handling, and diagnostic redaction. Manifest hashes have been refreshed and the full pipeline is being rechecked.


## Implementation log — 2026-11-13 (request reliability validation)
- GitHub Actions run 43358456440 passed all 69 regression tests, Python compilation, frontend JavaScript syntax, all 8 update manifest hashes, Windows PowerShell parser validation, and release source/channel gate.
- The candidate now has serialized writes, bounded request timeouts, and local diagnostics that deliberately omit request payloads, URLs, and credential-bearing exception text.


## Implementation log — 2026-11-13 (bilingual prompt assets)
- Cases and reusable modules now support an optional separate English prompt body while preserving the original primary-language text. The editor can maintain both versions, the detail view switches between them, and the English version has its own copy action.
- The prompt composer can optionally append saved English versions for the base case and selected modules. Missing English text is omitted rather than machine-translated or fabricated; this iteration provides bilingual storage/reuse, not automatic translation.
- Added integration guards for bilingual editor fields, detail tabs, copy action, and composer output. Full runtime hash and regression validation are pending.


## Implementation log — 2026-11-13 (bilingual prompt validation)
- GitHub Actions run 43358645528 passed all 69 regression tests, Python compilation, frontend JavaScript syntax, all 8 incremental-update SHA-256 checks, Windows PowerShell parser validation, and release source/channel gate.
- The case editor, detail view, copy action, and composer now support separately stored English prompt bodies. No automatic translation is claimed; absent English versions are omitted.


## Implementation log — 2026-11-13 (model-specific composition guidance)
- Added optional composer guidance profiles for WAN 3.0, MiniMax H3, and Seedance 2.5. WAN guidance emphasizes source-video identity/action/camera/background locking and localized garment edits; H3 guidance emphasizes chronological action progression and concise constraints; Seedance guidance emphasizes reference-role separation and cross-frame continuity.
- RunningHub is explicitly described as a hosting/runtime platform, not a video model; the composer asks the user to identify the actual model before applying model-specific constraints.
- Guidance is labelled as general workbench advice, not official vendor parameters or a guarantee. Added Seedance to the selectable model list and integration regression guards. Manifest hash refresh and full CI validation are in progress.


## Implementation log — 2026-11-13 (curated retrieval benchmark)
- Added a labelled garment retrieval benchmark covering a black nylon vest, dark-grey corduroy flared trousers, navy wool outerwear, and a brown leather jacket, plus a conflicting red/cashmere outerwear query.
- The benchmark checks that each positive natural-language query returns its expected verified case at or above the configured 80% ranking threshold, while the conflicting negative query returns no candidate.
- GitHub Actions run 43358913984 passed all 70 regression tests, Python compilation, frontend JavaScript syntax, all 8 update-file hashes, Windows PowerShell parser validation, and release source/channel gate.


## Implementation log — 2026-11-13 (platform/model compatibility correction)
- Corrected prompt-composition compatibility checks so RunningHub is treated as a hosting platform rather than a concrete model. A base case labelled RunningHub can now be composed with a module targeting WAN 3.0 or another actual model without a false incompatibility warning.
- Added a regression guard for the platform/model distinction. Runtime hash refresh and CI revalidation are pending.


## Implementation log — 2026-11-13 (AI endpoint transport security)
- AI analysis now rejects plain HTTP endpoints for non-loopback hosts, requiring HTTPS before sending the API Key. Plain HTTP remains available for localhost/127.0.0.1/::1 development services.
- Added tests for allowed loopback HTTP and rejected remote HTTP endpoints. This reduces the risk of transmitting provider credentials in cleartext on a network; the updated analyzer hash and full test suite are being revalidated.


## Implementation log — 2026-11-13 (Windows-native CI smoke coverage)
- Expanded the Windows runner job beyond PowerShell syntax parsing: it now executes dedicated Windows runtime tests for the local HTTP server, version/data APIs, full ZIP backup, process restart/data persistence, verified updater installation, and injected mid-transaction failure rollback.
- The updater now removes leftover `.update_tmp` and `.restore_tmp` files after a failed transaction rollback; the Windows regression test asserts cleanup as well as restoration of the original program files.
- These are automated Windows-runner checks, not a substitute for testing on the user's own Windows installation. CI execution and any failures will be inspected before treating this gate as passed.


## Implementation log — 2026-11-13 (Windows runner smoke tests executed)
- Windows Server 2025 CI run 43359383841 executed all three new runtime smoke tests successfully: local API + full backup + restart/data preservation; verified update installation/backup; injected mid-transaction failure with original files restored and no leftover temp files.
- The updater's rollback cleanup is now covered on Windows. The same run's cross-platform validation stopped only at the expected stale updater SHA-256 manifest gate; the updater hash has since been refreshed and the complete pipeline is being rerun.
- These tests provide automated Windows runner evidence, but user-machine acceptance remains a separate final gate.


## Implementation log — 2026-11-13 (Windows runner and rollback gate passed)
- GitHub Actions run 43359461774 passed 74 regression tests and all release validation jobs.
- The Windows Server 2025 job passed all three runtime tests: local API/full backup/restart data preservation; verified update install and backup retention; and injected mid-update failure with rollback, original-file restoration, and temporary-file cleanup.
- The Ubuntu regression suite also passed the same three smoke tests, bringing the combined regression count to 74. All 8 runtime update files matched their SHA-256 manifest entries, and the release source/channel gate passed.
- This materially improves automated Windows confidence, but it is not yet acceptance on the user's own desktop, Windows account, antivirus configuration, or existing data directory.


## Implementation log — 2026-11-13 (actual Windows BAT lifecycle verified)
- Added a Windows-only integration test that launches the real `.bat` file through `cmd.exe`, starts the actual application server on port 8765, verifies `/api/version`, sends a heartbeat, requests client close, and confirms the launcher process exits cleanly.
- The first run proved the server started and responded but exposed a Windows console-code-page decoding issue in the test harness; the harness now reads raw bytes and decodes safely. The rerun passed.
- GitHub Actions run 43359752504 passed 75 cross-platform regression tests (one Windows-only test skipped on Linux), plus four Windows Server 2025 runtime tests, all 8 update-file hashes, PowerShell parsing, and release source/channel gate. The four Windows tests include actual BAT launch/close, local backup/restart preservation, updater success, and updater rollback.


## Implementation log — 2026-11-13 (DOM injection and import integrity hardening)
- Security review found that a few library cards and copy buttons interpolated imported asset IDs or prompt text directly into inline JavaScript handlers. Replaced these with escaped data attributes and event listeners so imported content remains data, not executable code.
- Prompt vault and composer IDs now use escaped attribute values; case/module cards open through DOM listeners; prompt copy buttons read from escaped data attributes.
- JSON import now deduplicates IDs across the entire incoming batch (not only against pre-existing records), skips malformed entries, and restores the pre-import dataset if saving fails. English prompt text is included in case/module library search.
- Added integration regression guards for DOM-safe ID rendering and import rollback. Runtime hash refresh and full CI rerun are pending.


## Implementation log — 2026-11-13 (ambiguous save timeout reconciliation)
- Hardened save timeout handling: a client-side timeout no longer falsely claims the original data was restored, because the local server may have committed the write before the response was lost.
- Timed-out saves now wait for any queued writes, then reload the authoritative local server dataset. The UI distinguishes confirmed server state, confirmed rollback after ordinary errors, and unresolved state when reconciliation itself cannot complete.
- Applied this behavior to single-asset edits/deletes, batch case/module operations, and JSON imports. Added integration guards for reconciliation and truthful timeout messaging.


## Implementation log — 2026-11-13 (full backup restore)
- Added verified full ZIP backup restore. Before changing live data, the restore path validates the backup format/version, exact manifest file list, safe relative paths, archive/file-count/uncompressed-size bounds, every file SHA-256, SQLite integrity/schema, and JSON asset structure/unique IDs.
- Restore stages files under the local data directory, creates an automatic pre-restore full safety backup, then replaces the database, JSON export and reference images. If any swap or post-restore validation fails, it attempts to restore the previous database, JSON and references.
- Added a streaming ZIP upload endpoint (500MB compressed cap; no full-archive buffering in RAM) and a settings-page restore flow with explicit confirmation and a 10-minute request timeout.
- Added storage tests for successful round-trip restore, tampered archive rejection without live-data mutation, and ZIP path-traversal rejection. Windows smoke test now exercises backup, mutation, restore, automatic safety snapshot, restart and data persistence.


## Implementation log — 2026-11-13 (full backup restore passed Windows runtime)
- GitHub Actions run 43361168587 passed 78 regression tests, all 8 runtime update-file SHA-256 checks, Windows PowerShell parser validation, release source/channel gate, and all four Windows Server 2025 runtime tests.
- The Windows local-server test now creates a full ZIP backup, mutates current data and references, restores the archive through the actual streaming HTTP endpoint, verifies the automatic pre-restore safety backup, restarts the service, and confirms restored data persists. The actual BAT launch/heartbeat/close test and updater install/rollback tests also passed.
- Backup UI explicitly warns that SHA-256 checks detect file corruption but do not cryptographically authenticate who created the archive; users should restore only trusted backups.


## Implementation log — 2026-11-13 (Windows-safe backup archive validation)
- Hardened full-backup restore before extraction: rejects Windows alternate-data-stream colons, reserved device names (CON/PRN/AUX/NUL/COM/LPT1–9), trailing dots/spaces, symbolic-link ZIP entries, and case-insensitive path collisions. Payload paths are restricted to the expected database, JSON, and references locations.
- Added regression cases for Windows path aliases, reserved names, trailing dot/space and case-insensitive collisions. Both new tests pass.
- GitHub Actions run 43361584411 passed 81 regression tests, all 8 runtime SHA-256 checks, Windows Server 2025's four runtime tests (including actual BAT lifecycle, backup/restore persistence, and updater install/rollback), PowerShell syntax validation, and the release source/channel gate.
- The branch remains a candidate. The user's own Windows installation and existing-data directory still require final acceptance before V1.0 release.


## Implementation log — 2026-11-13 (full restore security and rollback verified)
- ZIP restore now also rejects Windows-specific path hazards: alternate data streams (`:`), reserved device names (CON/PRN/AUX/NUL/COM1–9/LPT1–9), trailing dots/spaces, case-insensitive path collisions, symlink entries, backslashes, NULs, and files outside the expected database/JSON/references roots.
- Added tests for Windows path aliases/reserved names, ZIP traversal, tampering, successful restore, and a deliberately injected failure during the database/JSON/reference swap to verify rollback keeps the pre-restore live dataset intact.
- GitHub Actions run 43361584411 passed 81 regression tests, all 8 update manifest hashes, PowerShell parsing, release source/channel gate, and all four Windows Server 2025 runtime tests (actual BAT lifecycle, backup/restore/restart, updater success, updater rollback).


## Implementation log — 2026-11-13 (backup payload cross-consistency)
- Full-backup restore now checks that the JSON export's complete case/module records exactly match the records inside the SQLite snapshot, not merely that both contain the same number of rows. This catches a ZIP whose JSON was altered and whose internal SHA-256 manifest was recomputed while the database remained different.
- Added a regression test that modifies a prompt while preserving record count and recalculates the ZIP's SHA-256 manifest; restore correctly rejects the archive and leaves live data untouched.
- GitHub Actions run 43361808990 passed 82 regression tests, all 8 runtime manifest hashes, four Windows Server 2025 runtime tests, PowerShell syntax validation, and the release source/channel gate.


## Implementation log — 2026-11-13 (generation outcome feedback loop)
- Added explicit per-case generation outcome recording. From the case detail view, the user can mark a real reuse attempt as successful or failed and optionally record the observed result/failure cause.
- Successful outcomes promote the case to `已验证成功`; failed outcomes mark it `失败案例`. Both transitions preserve the prior asset snapshot through the existing revision system and append a local generation-feedback record.
- Retrieval analytics now includes generation success/failure counts alongside helpful/not-relevant retrieval feedback. The system records these labels but does not automatically change ranking weights without a validated benchmark.


## Implementation log — 2026-11-13 (generation feedback loop validated)
- GitHub Actions run 43362087830 passed 85 regression tests, all 8 runtime manifest hashes, Windows PowerShell parsing, release source/channel gate, and all four Windows Server 2025 runtime tests.
- The generation feedback flow now changes case status to verified-success or failed, stores the outcome and notes locally, retains the pre-outcome asset revision, and exposes success/failure totals in retrieval analytics. The system records feedback without silently changing ranking weights.


## Implementation log — 2026-11-13 (generation outcome feedback loop and transactional integrity)
- Case detail now records whether a generated result succeeded or failed, with an optional note. Reuse outcome updates the case status, stores a timestamp and adds a local analytics record; aggregate success/failure counts appear in data analytics.
- Made the case status, immutable revision snapshot, and generation-feedback row a single SQLite transaction. A feedback-write failure now rolls back the status change and revision together, rather than leaving a half-recorded outcome.
- Restoring an older full backup re-runs additive schema initialization after the database swap, so newly introduced local analytics tables/indexes are recreated without replacing the restored case data.
- Full backup filenames now include microseconds to prevent rapid or concurrent snapshots in the same second from overwriting one another.
- GitHub Actions run 43362277749 passed 87 regression tests, all 8 runtime SHA-256 hashes, four Windows Server 2025 runtime tests, PowerShell parsing, and release source/channel gate.


## Implementation log — 2026-11-13 (generation feedback atomicity and backup schema migration)
- Generation outcome updates now run in one SQLite transaction: prior asset snapshot, case status/last-outcome metadata, and generation-feedback event are committed together. The feedback table is bounded to 5,000 records.
- Full backup restore now reapplies additive schema initialization after installing the restored database, allowing older backups to gain newer local tables/indexes before the app uses them. Added a regression test that restores a database missing the generation-feedback table and verifies the feature works afterward.
- GitHub Actions run 43362347918 passed 87 regression tests, all 8 update-manifest hashes, PowerShell parsing, release source/channel gate, and all four Windows Server 2025 runtime smoke tests.


## Implementation log — 2026-11-13 (opt-in automatic installation)
- Added a persistent `auto_update_enabled` setting and local API to control it. Automatic installation is off by default and requires one explicit user opt-in in Settings.
- When enabled, startup and 15-minute update checks automatically invoke the verified update pipeline if a newer version is available. The updater still creates a pre-update full backup, validates staged files against the manifest, shuts down/restarts the app, and uses transactional rollback.
- The Settings page shows whether automatic installation is authorized and lets the user disable it. Manual update confirmation remains the default when authorization is off.
- This implements the intended no-recurring-download workflow without silently installing updates before the user explicitly enables it.


## Implementation log — 2026-11-13 (automatic update authorization validated)
- GitHub Actions run 43362937490 passed all 87 regression tests, all 8 update-manifest SHA-256 checks, PowerShell parser validation, the release source/channel gate, and all four Windows Server 2025 runtime smoke tests.
- The automatic update feature is opt-in and persists locally. Once enabled, the app automatically downloads/applies a newer candidate from the configured update channel after manifest verification and pre-update backup; it restarts itself. With the setting off, updates remain manual.


## Implementation log — 2026-11-13 (automatic update polling cadence)
- Tightened the opt-in automatic update polling interval from six hours to 15 minutes, while retaining the immediate startup check. The manifest request is lightweight; actual installation still occurs only when the user has explicitly enabled automatic updates and the update manifest/file hashes validate.


## Implementation log — 2026-11-13 (safe idle-aware automatic updates)
- Automatic update checks now poll every 15 minutes and immediately on startup. When automatic installation is authorized and a new version is available, installation waits for 15 minutes of continuous inactivity, requires the workbench to be visible/focused, then displays a 60-second countdown.
- Pointer, keyboard, touch, and visibility activity reset the idle timer. The user can cancel the pending install; the next new version will be handled under the authorization rule. The app still creates a full pre-update backup and verifies every staged file before replacement.
- This avoids interrupting an active editing session while preserving the opt-in no-manual-download workflow.
