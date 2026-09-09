# Reliability investigation — 2026-09-09

## Scope and results

Cloned the repository and installed backend/frontend dependencies locally. No authenticated account writes were performed: a Bluesky app password is still needed. AI provider calls were simulated; no generation API key was supplied for this test.

- Backend regression suite: 9 passed (`.venv/bin/python -m pytest -q`).
- Original HEAD comparison: 7 failed, 1 passed, 1 deselected. The deselected test covers the new worker recovery wrapper, which does not exist in the original.
- Frontend: TypeScript check and Vite production build passed (`cd frontend && npm run build`).
- Browser smoke check: login form renders and rejects missing credentials.
- Backend HTTP health check: `GET /` returns `{"status":"ok"}`.
- Read-only live check: the latest 30 image posts returned for jsherman999.bsky.social had matching PDS and public-feed record alt text. This sample does not prove that future writes will become publicly visible.

## Defects addressed

1. A single failed queue polling request permanently cleared the frontend timer. Polling now continues after transient errors and prevents overlapping requests.
2. Each post blocked on up to 12 public verification requests, retry sleeps, and additional diagnostic requests. Confirmed writes now receive one public check and then enter the existing propagation queue.
3. Verification accepted repository record alt text even when rendered post images could differ. It now checks image views in the public post thread, including quote posts with images.
4. Propagation checks always selected the first pending post. Checks now rotate by attempt count, with six total verification attempts per delayed post (including the initial check), and report unconfirmed public visibility explicitly.
5. Failed login or an unexpected worker exit could leave a dead runtime that Resume treated as alive. Exited workers now release runtime state; unexpected failures requeue running items and pause visibly.
6. AI calls used the SDK's long default timeout and swallowed errors. Calls now use a 30-second request timeout with at most one SDK retry, and expose provider errors to generation job status.
7. Two existing frontend type errors were fixed; the build now includes type checking.

Tests exercise nested image views, false public success, one write per post, fair verification, a 12-post queue with permanently stale public data, worker failure recovery, login failure, and generation continuing after a provider failure.

## Remaining live validation

Enter a Bluesky app password into the local UI, scan, and test a small set of accurately described images. Verify both the account PDS record and the publicly rendered image alt text. Avoid treating a successful PDS write as public success. No posts need to be deleted or recreated for this investigation.

The protocol permits repository updates, but public visibility is separate. Historical upstream reports describe Bluesky ignoring edits to existing posts:
https://github.com/bluesky-social/atproto/discussions/3038
This is a possible platform limitation, not a confirmed diagnosis from the 30-post sample.

## Regeneration during a batch

The UI now disables individual regeneration until batch generation finishes or
acknowledges Stop Generation. Manual regeneration requests are also serialized,
and a new scan cannot overlap generation. Generation polling retries transient
errors without pretending the batch stopped; overlapping polls and duplicate
events are ignored. Image status displays provider errors even when a prior
suggestion exists. A successful regeneration updates the unedited draft and
preserves manually edited text.

Verified in the browser with `tests/ui_generation_fixture.py` and an isolated
Vite instance (no real credentials or provider calls):

- A simulated 503 polling failure leaves the batch active and regeneration disabled.
- Stop Generation completes and enables regeneration.
- A simulated provider failure displays its error beside status while keeping the old suggestion.
- Retrying replaces the old unedited draft with the new suggestion.
- Regenerating again preserves a manually edited draft.

All 14 backend tests and the frontend type check/production build also pass.
The original user's provider error was not visible in the inspected tab, so its
specific cause has not been confirmed.

## Discard drafts and regenerate

19 backend tests pass. New tests verify authoritative stored alt text is
preserved, cached drafts are cleared, remote verification failures leave drafts
untouched, other-account records are rejected, and unfinished generation/apply
jobs block the reset. Browser fixture verification confirmed unpublished manual
edits disappear, stored Bluesky text remains, and a fresh generation batch starts.
The TypeScript check and production build pass. These reset tests use simulated
Bluesky records; no real account drafts or posts were discarded during testing.
