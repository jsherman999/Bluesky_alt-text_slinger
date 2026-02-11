# PDS Inspect Script

The script `scripts/pds_inspect.sh` provides CLI inspection tools for Bluesky DID/PDS/public view data.

## Commands

- `login`: create a session against the account PDS.
- `whoami`: show saved session metadata.
- `resolve`: resolve handle to DID.
- `get-record`: fetch one record from PDS.
- `list-records`: list records from PDS.
- `compare`: compare one post across PDS and public APIs.
- `watch-compare`: poll compare repeatedly until convergence or timeout.

## Prerequisite

- `jq` must be installed.

## Usage

```bash
# Login
scripts/pds_inspect.sh login --handle joocifer.bsky.social --app-password 'xxxx-xxxx-xxxx-xxxx'

# Resolve DID
scripts/pds_inspect.sh resolve --handle joocifer.bsky.social

# Get one post record from PDS
scripts/pds_inspect.sh get-record --handle joocifer.bsky.social --rkey 3mejhg2vgac2o

# List latest records
scripts/pds_inspect.sh list-records --handle joocifer.bsky.social --limit 10

# Compare one post across views
scripts/pds_inspect.sh compare --handle joocifer.bsky.social --rkey 3mejhg2vgac2o

# Watch until converged or timeout
scripts/pds_inspect.sh watch-compare \
  --handle joocifer.bsky.social \
  --rkey 3mejhg2vgac2o \
  --interval 10 \
  --timeout 900
```

## Optional environment

- `PDS_ENDPOINT`: manually override discovered PDS endpoint.

Example:

```bash
PDS_ENDPOINT=https://ganoderma.us-west.host.bsky.network \
  scripts/pds_inspect.sh compare --did did:plc:zvqazsaifl3ywla5dh5tpowv --rkey 3mejhg2vgac2o
```

## Output fields (compare)

- `pds_cid`: CID from account PDS record.
- `public_repo_cid`: CID from `public.api.bsky.app` record.
- `pds_alts`: alt values in PDS record.
- `public_repo_alts`: alt values in public repo read.
- `public_thread_record_alts`: alt values in public thread record.
- `public_posts_view_alts`: alt values in public post view embed.

Convergence usually means CID and alt values match across PDS and public views.
