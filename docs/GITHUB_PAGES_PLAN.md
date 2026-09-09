# GitHub Pages version — proposed plan

Status: for review; migration and deployment have not started.

## Intended result

Serve the React app from GitHub Pages. Scanning, model discovery, generation,
editing, application, and verification run in the user's browser. No Python
server or SQLite database is required for this version. Keep the local version
available while validating the browser implementation.

Credentials, tokens, drafts, and queues remain in page memory. Do not write them
to localStorage, sessionStorage, IndexedDB, cookies, files, logs, or the repository.
Refresh or closing the page ends the session and loses unsaved progress. Completed
Bluesky updates remain on Bluesky. Provider requests are still subject to each
provider's own retention policies.

## Proposed implementation

1. **Separate the UI from execution.** Introduce a typed service interface for
   scan, discover models, generate, apply, pause, stop, and progress. Keep the
   current backend adapter and add a browser adapter. Select the adapter at build
   time so the Pages build never calls localhost or `/api` routes.

2. **Add browser authentication and account discovery.** For the first version,
   use a Bluesky handle and app password with the AT Protocol JavaScript client.
   Resolve the account's server, authenticate directly, keep session tokens in
   memory, and refresh tokens during long jobs. Release the password after login.
   An explicit Disconnect action cancels work and clears session state. Do not
   enable any SDK persistence hooks. OAuth is a later option: its redirect and
   session storage behavior must be designed and tested against the strict
   no-persistent-storage requirement before adopting it.

3. **Port scanning and generation.** Fetch paginated account posts, including
   image quote posts, and ignore reposts by other authors. Preserve current
   selection and draft editing behavior. Make model discovery and generation
   requests directly to OpenAI or OpenRouter using the user's key. Verify CORS
   for each actual endpoint from the Pages origin. Never silently add a proxy if
   a provider blocks browser access; report the limitation and retain manual
   description entry. Filter models for image understanding and preserve useful
   errors without exposing credentials.

4. **Run bounded queues in the page.** Use one generation request at a time;
   individual regeneration waits until the batch stops. Provide cancellation,
   bounded timeouts, rate-limit backoff, and live progress without overlapping
   jobs. Handle tab suspension and network loss explicitly. Tell users to keep
   the tab open; a browser cannot guarantee background execution. Offer optional
   user-triggered export/import of drafts without credentials only if requested.

5. **Port safe application and verification.** Group image edits per post,
   preserve all other record fields, and use CID compare-and-swap. Verify the
   stored record separately from the publicly rendered image description. Keep
   bounded, fair public verification and clearly distinguish confirmed success
   from saved-but-not-publicly-visible updates. Never delete/recreate posts as
   a workaround. A browser port does not resolve Bluesky post-edit limitations.

6. **Prepare Pages deployment.** Configure Vite's project base path as
   `/Bluesky_alt-text_slinger/`; use a single entry page or hash navigation to avoid
   deep-link 404s. Add a GitHub Actions workflow to install locked dependencies,
   run checks, build, and deploy only the static output. No user or shared API
   keys belong in workflow secrets or build variables. Review dependencies and
   minimize third-party scripts because page scripts can access in-memory keys.
   Publish only after the implementation and acceptance checks are reviewed.

## Acceptance checks

- Works from the real HTTPS Pages origin with no local server running.
- Browser storage remains free of credentials and application state before,
  during, and after use; credential values never appear in URLs or logs.
- Requests containing credentials go only to the selected authentication or
  provider endpoints, never GitHub or an application relay.
- Refresh and Disconnect clear state; a provider key is required again afterward.
- Model discovery and generation tested with user-provided provider keys; no
  shared key embedded in bundled assets.
- A batch of at least 20 images survives simulated timeouts, 429s, disconnection,
  and tab suspension without duplicate writes or permanently frozen progress.
- Regression coverage for stop/regenerate, manual draft preservation, nested
  image embeds, CID conflicts, and public verification failure.
- Small authenticated Bluesky write test checks both stored and visible alt
  text. If public visibility does not update, document the limitation before
  presenting the app as reliable for retroactive edits.
- Responsive layout, keyboard controls, and accessible status announcements.

## Review decisions

Recommended first release: memory-only app-password login, OpenAI/OpenRouter,
manual alt entry, and no automatic persistence or background operation. Keep
OAuth, draft export, and browser-local AI inference outside this first release.

The main feasibility gates are provider CORS and Bluesky's handling of edits to
existing posts. Complete those checks before investing in the full migration.

## References

- [GitHub Pages static hosting](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)
- [AT Protocol browser OAuth](https://github.com/bluesky-social/atproto/blob/main/packages/api/OAUTH.md)
- [OpenRouter browser authentication](https://openrouter.ai/docs/guides/overview/auth/oauth)
- [Upstream discussion of post-edit visibility](https://github.com/bluesky-social/atproto/discussions/3038)
