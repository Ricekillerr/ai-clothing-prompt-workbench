# Maintenance and Legacy File Cleanup

## Cleanup policy
This repository has historical source files from early prototypes. Cleanup is performed only on the isolated stabilization branch until the production release is approved.

## Current-source dependency audit (legacy branch snapshot)
- `ai_prompt_workbench.py` imports `storage.py`, `collector.py`, `case_matcher.py`, and `ai_analyzer.py`.
- The current launcher is `启动AI服装提示词工作台.bat`.
- The update allowlist explicitly includes the current launcher and `使用说明_AI服装提示词资产工作台_V0.4.3.txt`.
- `update_manifest.json` lists only the 0.5.2 release payload on this branch snapshot.
- The older `ai_prompt_workbench_v0_3.py` and `启动AI服装提示词工作台_V0.3.bat` are not referenced by the current main program or current launcher in this snapshot. They are historical prototype artifacts, not candidates for the active update payload.

## Safe cleanup decision
Do not permanently delete legacy files until the V0.6.5 source has been synchronized and a complete reference scan has been run against that exact source. On synchronization:
1. Move obsolete prototype files and superseded user guides to `docs/archive/`.
2. Keep any migration fixtures or compatibility files required by storage tests.
3. Keep the current launcher, updater allowlist files, and current user guide.
4. Rebuild the update manifest only after the final payload is frozen.
5. Verify every manifest path and SHA-256 against the exact Git branch content.

## Release blocker
The branch snapshot currently contains APP_VERSION 0.5.2 and update_manifest version 0.5.2. It is not a V0.6.5 release source. Do not publish it as V0.6.5 or merge it to the production update channel.
