#!/bin/zsh
set -euo pipefail

# Usage examples:
#   scripts/pds_inspect.sh login --handle joocifer.bsky.social --app-password 'xxxx-xxxx-xxxx-xxxx'
#   scripts/pds_inspect.sh compare --handle joocifer.bsky.social --rkey 3mejhg2vgac2o
#   scripts/pds_inspect.sh get-record --did did:plc:... --rkey 3mejhg2vgac2o
#   scripts/pds_inspect.sh list-records --handle joocifer.bsky.social --limit 10
#
# Optional env:
#   PDS_ENDPOINT=https://ganoderma.us-west.host.bsky.network

PUBLIC_API="https://public.api.bsky.app"
CACHE_DIR="${HOME}/.cache/alttext-slinger"
SESSION_FILE="${CACHE_DIR}/session.json"
mkdir -p "$CACHE_DIR"

cmd="${1:-}"
shift || true

arg() {
  local name="$1"
  shift
  local v=""
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == "$name" ]]; then
      v="$2"
      echo "$v"
      return 0
    fi
    shift
  done
  return 1
}

require_jq() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "error: jq is required (brew install jq)" >&2
    exit 1
  fi
}

normalize_handle() {
  local h="$1"
  echo "${h#@}"
}

resolve_handle() {
  local handle
  handle="$(normalize_handle "$1")"
  curl -sS "${PUBLIC_API}/xrpc/com.atproto.identity.resolveHandle?handle=${handle}"
}

discover_pds() {
  local did="$1"
  local endpoint
  endpoint="$(curl -sS "https://plc.directory/${did}" | jq -r '.service[]? | select(.type=="AtprotoPersonalDataServer") | .serviceEndpoint' | head -n1)"
  if [[ -z "$endpoint" || "$endpoint" == "null" ]]; then
    echo "error: could not discover PDS endpoint for ${did}" >&2
    exit 1
  fi
  echo "$endpoint"
}

login() {
  require_jq
  local handle app_password
  handle="$(arg --handle "$@")"
  app_password="$(arg --app-password "$@")"
  if [[ -z "$handle" || -z "$app_password" ]]; then
    echo "usage: $0 login --handle HANDLE --app-password APP_PASSWORD" >&2
    exit 1
  fi

  handle="$(normalize_handle "$handle")"

  local did
  did="$(resolve_handle "$handle" | jq -r '.did')"
  if [[ -z "$did" || "$did" == "null" ]]; then
    echo "error: failed to resolve DID for ${handle}" >&2
    exit 1
  fi

  local pds
  pds="${PDS_ENDPOINT:-$(discover_pds "$did")}"

  local body
  body="$(jq -n --arg identifier "$handle" --arg password "$app_password" '{identifier:$identifier,password:$password}')"

  local sess
  sess="$(curl -sS -X POST "${pds}/xrpc/com.atproto.server.createSession" \
    -H 'Content-Type: application/json' \
    -d "$body")"

  local access did_out
  access="$(echo "$sess" | jq -r '.accessJwt // empty')"
  did_out="$(echo "$sess" | jq -r '.did // empty')"
  if [[ -z "$access" || -z "$did_out" ]]; then
    echo "error: login failed" >&2
    echo "$sess" | jq
    exit 1
  fi

  jq -n --arg handle "$handle" --arg did "$did_out" --arg pds "$pds" --argjson session "$sess" \
    '{handle:$handle,did:$did,pds:$pds,session:$session,created_at:(now|todate)}' > "$SESSION_FILE"

  echo "logged in"
  echo "handle: $handle"
  echo "did:    $did_out"
  echo "pds:    $pds"
  echo "session: $SESSION_FILE"
}

load_session() {
  require_jq
  if [[ ! -f "$SESSION_FILE" ]]; then
    echo "error: no session found. run: $0 login --handle ... --app-password ..." >&2
    exit 1
  fi
}

get_record() {
  require_jq
  local did rkey collection pds
  did="$(arg --did "$@")"
  rkey="$(arg --rkey "$@")"
  collection="$(arg --collection "$@")"
  collection="${collection:-app.bsky.feed.post}"

  if [[ -z "$did" ]]; then
    local handle
    handle="$(arg --handle "$@")"
    if [[ -n "$handle" ]]; then
      did="$(resolve_handle "$handle" | jq -r '.did')"
    fi
  fi

  if [[ -z "$did" || -z "$rkey" ]]; then
    echo "usage: $0 get-record (--did DID | --handle HANDLE) --rkey RKEY [--collection app.bsky.feed.post]" >&2
    exit 1
  fi

  pds="${PDS_ENDPOINT:-$(discover_pds "$did")}"
  curl -sS "${pds}/xrpc/com.atproto.repo.getRecord?repo=${did}&collection=${collection}&rkey=${rkey}" | jq
}

list_records() {
  require_jq
  local did handle limit collection pds
  did="$(arg --did "$@")"
  handle="$(arg --handle "$@")"
  limit="$(arg --limit "$@")"
  collection="$(arg --collection "$@")"
  limit="${limit:-25}"
  collection="${collection:-app.bsky.feed.post}"

  if [[ -z "$did" && -n "$handle" ]]; then
    did="$(resolve_handle "$handle" | jq -r '.did')"
  fi

  if [[ -z "$did" ]]; then
    echo "usage: $0 list-records (--did DID | --handle HANDLE) [--collection app.bsky.feed.post] [--limit 25]" >&2
    exit 1
  fi

  pds="${PDS_ENDPOINT:-$(discover_pds "$did")}"
  curl -sS "${pds}/xrpc/com.atproto.repo.listRecords?repo=${did}&collection=${collection}&limit=${limit}" | jq
}

compare_json() {
  require_jq
  local did handle rkey collection pds
  did="$(arg --did "$@")"
  handle="$(arg --handle "$@")"
  rkey="$(arg --rkey "$@")"
  collection="$(arg --collection "$@")"
  collection="${collection:-app.bsky.feed.post}"

  if [[ -z "$did" && -n "$handle" ]]; then
    did="$(resolve_handle "$handle" | jq -r '.did')"
  fi

  if [[ -z "$did" || -z "$rkey" ]]; then
    echo "usage: $0 compare (--did DID | --handle HANDLE) --rkey RKEY [--collection app.bsky.feed.post]" >&2
    exit 1
  fi

  pds="${PDS_ENDPOINT:-$(discover_pds "$did")}"
  local at_uri
  at_uri="at://${did}/${collection}/${rkey}"

  local pds_json pub_repo_json pub_thread_json pub_posts_json
  pds_json="$(curl -sS "${pds}/xrpc/com.atproto.repo.getRecord?repo=${did}&collection=${collection}&rkey=${rkey}")"
  pub_repo_json="$(curl -sS "${PUBLIC_API}/xrpc/com.atproto.repo.getRecord?repo=${did}&collection=${collection}&rkey=${rkey}")"
  pub_thread_json="$(curl -sS "${PUBLIC_API}/xrpc/app.bsky.feed.getPostThread?uri=${at_uri}&depth=0&parentHeight=0")"
  pub_posts_json="$(curl -sS "${PUBLIC_API}/xrpc/app.bsky.feed.getPosts?uris=${at_uri}")"

  jq -n \
    --arg at_uri "$at_uri" \
    --arg did "$did" \
    --arg pds "$pds" \
    --argjson pdsj "$pds_json" \
    --argjson pubr "$pub_repo_json" \
    --argjson pubt "$pub_thread_json" \
    --argjson pubp "$pub_posts_json" \
    '{
      uri:$at_uri,
      did:$did,
      pds:$pds,
      pds_cid: ($pdsj.cid // null),
      public_repo_cid: ($pubr.cid // null),
      pds_alts: (($pdsj.value.embed.images // []) | map({alt:(.alt // "")})),
      public_repo_alts: (($pubr.value.embed.images // []) | map({alt:(.alt // "")})),
      public_thread_record_alts: (($pubt.thread.post.record.embed.images // []) | map({alt:(.alt // "")})),
      public_posts_view_alts: (($pubp.posts[0].embed.images // []) | map({alt:(.alt // "")}))
    }'
}

compare() {
  compare_json "$@" | jq
}

watch_compare() {
  require_jq
  local interval timeout
  interval="$(arg --interval "$@")"
  timeout="$(arg --timeout "$@")"
  interval="${interval:-10}"
  timeout="${timeout:-900}"

  if ! [[ "$interval" =~ ^[0-9]+$ ]] || ! [[ "$timeout" =~ ^[0-9]+$ ]]; then
    echo "error: --interval and --timeout must be integer seconds" >&2
    exit 1
  fi

  local start now elapsed last_json
  start="$(date +%s)"
  while true; do
    last_json="$(compare_json "$@")"
    local pds_cid pub_cid pds_alts pub_repo_alts pub_posts_alts
    pds_cid="$(echo "$last_json" | jq -r '.pds_cid // ""')"
    pub_cid="$(echo "$last_json" | jq -r '.public_repo_cid // ""')"
    pds_alts="$(echo "$last_json" | jq -c '.pds_alts')"
    pub_repo_alts="$(echo "$last_json" | jq -c '.public_repo_alts')"
    pub_posts_alts="$(echo "$last_json" | jq -c '.public_posts_view_alts')"

    now="$(date +%s)"
    elapsed="$((now - start))"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] elapsed=${elapsed}s pds_cid=${pds_cid} public_cid=${pub_cid}"

    if [[ "$pds_cid" == "$pub_cid" && "$pds_alts" == "$pub_repo_alts" && "$pds_alts" == "$pub_posts_alts" ]]; then
      echo "converged: public views now match PDS"
      echo "$last_json" | jq
      return 0
    fi

    if (( elapsed >= timeout )); then
      echo "timeout: no convergence after ${timeout}s"
      echo "$last_json" | jq
      return 2
    fi
    sleep "$interval"
  done
}

whoami_cmd() {
  load_session
  jq '{handle,did,pds,created_at}' "$SESSION_FILE"
}

case "$cmd" in
  login) login "$@" ;;
  whoami) whoami_cmd ;;
  get-record) get_record "$@" ;;
  list-records) list_records "$@" ;;
  compare) compare "$@" ;;
  watch-compare) watch_compare "$@" ;;
  resolve)
    require_jq
    h="$(arg --handle "$@")"
    if [[ -z "$h" ]]; then
      echo "usage: $0 resolve --handle HANDLE" >&2
      exit 1
    fi
    resolve_handle "$h" | jq
    ;;
  *)
    cat <<USAGE
Usage:
  $0 login --handle HANDLE --app-password APP_PASSWORD
  $0 whoami
  $0 resolve --handle HANDLE
  $0 get-record (--did DID | --handle HANDLE) --rkey RKEY [--collection app.bsky.feed.post]
  $0 list-records (--did DID | --handle HANDLE) [--collection app.bsky.feed.post] [--limit 25]
  $0 compare (--did DID | --handle HANDLE) --rkey RKEY [--collection app.bsky.feed.post]
  $0 watch-compare (--did DID | --handle HANDLE) --rkey RKEY [--collection app.bsky.feed.post] [--interval 10] [--timeout 900]
USAGE
    exit 1
    ;;
esac
