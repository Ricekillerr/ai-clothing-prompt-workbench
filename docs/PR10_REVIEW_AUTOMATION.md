# PR #10 Codex Review Automation

## Scope

- Repository: `Ricekillerr/ai-clothing-prompt-workbench`
- PR: `#10`
- Head branch: `feature/stage2-knowledge-data`
- Base branch: `release/v0.6.5-stabilization`
- Event: `pull_request.synchronize` only
- No PR creation/edit/comment/review, ordinary push, schedule, or CI-completion trigger

## Execution

1. Revalidate the current PR state, branch names, and event Head SHA.
2. Inspect the complete Git tree and changed paths before checkout; stop on private-data or credential-like paths, truncated tree results, or GitHub API errors.
3. Serialize runs per PR and cancel stale runs when a newer commit arrives.
4. Check for a completed artifact named `pr10-review-<HEAD_SHA>` before starting Codex.
5. Check out the exact PR Head SHA and collect check-run, workflow-run, commit-status, and changed-file metadata.
6. Run OpenAI Codex in `:read-only` mode with `drop-sudo` on a disposable Ubuntu runner.
7. Validate the structured JSON report and upload JSON plus Markdown summary as a GitHub Actions artifact.

## Security

- Workflow permissions are read-only; the report job has no repository permissions.
- The Codex Action is pinned to v1.14 commit `306c6a196a99dcc6582530c396a19d2672d5e9ec`.
- Codex CLI is pinned to `0.171.0`.
- Checkout and artifact actions are pinned to verified commit SHAs.
- Repository-supplied `AGENTS.md`, `AGENTS.override.md`, and `.codex` config are removed from the temporary checkout to reduce prompt-injection risk.
- Private-data and credential-like paths cause a fail-closed stop before checkout.
- CI metadata is collected; CI logs are not downloaded and must be reported as metadata-only evidence.
- No PR comments, reviews, approvals, commits, pushes, merges, releases, or migrations are performed.
- Every Codex task remains `WAITING_APPROVAL`.

## Required GitHub Secret

Add a repository secret named `OPENAI_API_KEY` through Settings → Secrets and variables → Actions → New repository secret. Paste the key directly into GitHub; never put it in ChatGPT, a PR, a commit, or a repository file.

OpenAI API usage through this key is billed separately from a ChatGPT subscription. The connected GitHub file/PR tools cannot create repository secrets.

## Deduplication

GitHub Actions concurrency serializes PR #10 runs. A successful review artifact is the completed-SHA record and is retained for 90 days. Artifact lookup failure stops the run.

This is durable deduplication within the artifact retention window, not an indefinite external database. Permanent deduplication would require an approved durable state service or repository state write, neither of which is enabled by this read-only design.

## Separate ChatGPT Work Trigger

This workflow provides the Codex read-only audit and artifact. It does not create the native ChatGPT Work GitHub event task.

The separate Work task must use GitHub → Pull request activity → Commit updates, filtered to this repository and PR #10, with the exact head/base branch and open/unmerged conditions. It should independently inspect the same Head SHA, project-memory files, CI evidence, and Codex artifact. No polling substitute is configured.

## Activation

1. Review and merge this infrastructure-only PR into `main`.
2. Add the `OPENAI_API_KEY` repository secret in GitHub Settings.
3. Create the separate ChatGPT Work event task for PR #10 commit updates.
4. Push a new commit to PR #10 to perform the first end-to-end trigger test.
5. Verify the run, JSON artifact, SHA deduplication, and ChatGPT review before considering Codex repair automation.

PR #10 remains Draft. No business code, production data, or release branch is modified by this infrastructure PR.