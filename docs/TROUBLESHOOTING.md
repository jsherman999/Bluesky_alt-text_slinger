# Troubleshooting

This guide focuses on the most common operational issues for Alt Text Slinger.

## 1) App not reachable on localhost/LAN

### Check launchd status

```bash
./launchd/control.sh status
```

Expected: backend and frontend should show `state = running` and active PIDs.

### Restart services

```bash
./launchd/control.sh restart
./launchd/control.sh status
```

### Verify ports

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
lsof -nP -iTCP:5173 -sTCP:LISTEN
```

### Verify health endpoints

```bash
curl -sS http://127.0.0.1:8000/
curl -sSI http://127.0.0.1:5173
```

If backend health fails, inspect logs:

```bash
./launchd/control.sh logs
```

## 2) "Failed to fetch" in UI

Typical causes:

- Backend not running on port `8000`.
- Browser is hitting wrong host/port.
- LAN firewall/network segmentation.

Checks:

```bash
curl -sS http://127.0.0.1:8000/
curl -sS http://<your-lan-ip>:8000/
```

If localhost works but LAN does not, verify local firewall and router/AP isolation settings.

## 3) Apply queue paused due to Bluesky rate limit

Symptom in UI: pause reason mentions rate limit reset time.

What to do:

- Wait until reset time shown in queue status.
- Resume queue from UI or API.

API resume example:

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/apply/queue/resume/<job_id>?handle=<handle>&app_password=<app_password>"
```

## 4) Items stuck in `propagating`

Symptom:

- Status shows `propagating to Bluesky`
- Debug says PDS alts updated, public alts still empty.

Run compare from backend API:

```bash
curl -sS "http://127.0.0.1:8000/api/debug/alt-compare?uri=at://did:plc:.../app.bsky.feed.post/<rkey>" | jq
```

Interpretation:

- If `pds_cid != public_repo_cid` and PDS alts differ from public alts, write landed on PDS but public appview has not converged.

Use script watcher:

```bash
scripts/pds_inspect.sh watch-compare --handle <handle> --rkey <rkey> --interval 10 --timeout 900
```

## 5) Inspect one record directly on PDS vs public

```bash
# Resolve DID
scripts/pds_inspect.sh resolve --handle <handle>

# Compare one post
scripts/pds_inspect.sh compare --handle <handle> --rkey <rkey>
```

## 6) Confirm OpenAI/OpenRouter generation config

The backend enables generation when either key is present:

- `OPENAI_API_KEY`, or
- `OPENROUTER_API_KEY`

Check launchd backend env in plist:

```bash
cat ~/Library/LaunchAgents/com.jay.bluesky-alttext.backend.plist
```

After env changes:

```bash
./launchd/control.sh restart
```

## 7) UI scroll jumps unexpectedly

If page jumps while map updates:

- Ensure frontend is running latest code.
- Restart frontend service.

```bash
./launchd/control.sh restart
```

Then hard-refresh browser (`Cmd+Shift+R`).

## 8) SQLite quick checks

Inspect queue/job counts:

```bash
cd backend
sqlite3 alttext_slinger.db "select status,count(*) from apply_job_items group by status;"
sqlite3 alttext_slinger.db "select job_id,status,pause_reason,total_items,processed_items,success_items,failed_items from apply_jobs order by created_at desc limit 10;"
```

## 9) Common credential mistakes

- Handle can be either `@user.bsky.social` or `user.bsky.social`.
- App password is required (not account password).
- If auth fails, generate a fresh app password in Bluesky settings.

## 10) Minimal recovery runbook

```bash
./launchd/control.sh restart
curl -sS http://127.0.0.1:8000/
curl -sSI http://127.0.0.1:5173
./launchd/control.sh status
./launchd/control.sh logs
```

If services are healthy but apply still does not show in Bluesky, use `compare`/`watch-compare` to verify whether it is a PDS/public propagation issue.
