# Propagation Behavior: PDS vs Public Bluesky View

## Problem summary

A write can be accepted on the account PDS (`com.atproto.repo.putRecord`) while public Bluesky APIs still show the old post record.

Typical signature:

- PDS record contains new alt text.
- Public `com.atproto.repo.getRecord` shows old/empty alt.
- Public `app.bsky.feed.getPostThread` and `app.bsky.feed.getPosts` also show old/empty alt.
- `pds_cid != public_repo_cid` for the same `at://` URI.

## What this means

- Write/auth path is working.
- Post record was updated at PDS.
- Public appview/index path has not converged to that updated record.

This can look like a successful apply in local state while still not visible in Bluesky UI.

## How the app handles it

Apply queue statuses:

- `pending`: waiting to process.
- `running`: being written now.
- `propagating`: PDS accepted write, waiting for public convergence.
- `applied`: public verification succeeded.
- `failed`: write or verification failed.

The queue is rate-limit aware and can pause/resume.

## Debugging workflow

1. Use backend debug endpoint:

```bash
curl -sS "http://127.0.0.1:8000/api/debug/alt-compare?uri=at://did:plc:.../app.bsky.feed.post/<rkey>"
```

2. Or use script:

```bash
scripts/pds_inspect.sh compare --handle <handle> --rkey <rkey>
scripts/pds_inspect.sh watch-compare --handle <handle> --rkey <rkey>
```

3. If convergence never occurs, treat it as a propagation failure for that post update path.

## Operational guidance

- Do not treat local DB `applied` state alone as proof of user-visible success.
- Prefer public verification for final status.
- Surface propagation state clearly in UI so users can distinguish local write vs public visibility.
