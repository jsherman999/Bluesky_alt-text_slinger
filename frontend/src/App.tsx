import React, { useState } from "react";
import "./App.css";
import {
  scanImages,
  ScanResponse,
  PostInfo,
  ImageInfo,
  applyAltUpdates,
  AltUpdate,
  ApplyResponse
} from "./api";

function formatDate(dateStr?: string | null): string {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return dateStr;
  return d.toLocaleString();
}

type AltState = {
  apply: boolean;
  draftAlt: string;
};

type AltStateMap = {
  [key: string]: AltState;
};

function makeKey(uri: string, index: number): string {
  return `${uri}::${index}`;
}

type FilterMode = "all" | "missingAlt" | "hasAlt" | "selected";

const App: React.FC = () => {
  const [handle, setHandle] = useState("");
  const [appPassword, setAppPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applyMessage, setApplyMessage] = useState<string | null>(null);
  const [result, setResult] = useState<ScanResponse | null>(null);
  const [altState, setAltState] = useState<AltStateMap>({});
  const [filterMode, setFilterMode] = useState<FilterMode>("all");

  const initAltStateFromResult = (data: ScanResponse) => {
    const next: AltStateMap = {};
    data.posts.forEach((post) => {
      post.images.forEach((img) => {
        const key = makeKey(post.uri, img.index);
        const baseAlt =
          img.alt && img.alt.trim().length > 0 ? img.alt : img.generated_alt || "";
        next[key] = {
          apply: !img.alt || img.alt.trim().length === 0, // default: auto-select only images with no existing alt
          draftAlt: baseAlt
        };
      });
    });
    setAltState(next);
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setApplyMessage(null);
    setResult(null);
    setAltState({});

    if (!handle || !appPassword) {
      setError("Please enter both handle and app password.");
      return;
    }

    setLoading(true);
    try {
      const data = await scanImages({
        handle,
        app_password: appPassword,
        generate_alt: true
      });
      setResult(data);
      initAltStateFromResult(data);
    } catch (err: any) {
      console.error(err);
      let msg = "An error occurred while scanning.";
      try {
        const parsed = JSON.parse(err.message);
        if (parsed?.detail) {
          msg =
            typeof parsed.detail === "string"
              ? parsed.detail
              : JSON.stringify(parsed.detail);
        }
      } catch {
        if (err.message) msg = err.message;
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleAltChange = (uri: string, index: number, value: string) => {
    const key = makeKey(uri, index);
    setAltState((prev) => ({
      ...prev,
      [key]: {
        apply: prev[key]?.apply ?? false,
        draftAlt: value
      }
    }));
  };

  const handleApplyToggle = (uri: string, index: number, apply: boolean) => {
    const key = makeKey(uri, index);
    setAltState((prev) => ({
      ...prev,
      [key]: {
        apply,
        draftAlt: prev[key]?.draftAlt ?? ""
      }
    }));
  };

  const onApplyChanges = async () => {
    if (!result) return;
    setApplyMessage(null);
    setError(null);

    const updates: AltUpdate[] = [];

    result.posts.forEach((post: PostInfo) => {
      post.images.forEach((img: ImageInfo) => {
        const key = makeKey(post.uri, img.index);
        const state = altState[key];
        if (!state || !state.apply) return;
        const draft = state.draftAlt?.trim();
        if (!draft) return;
        updates.push({
          uri: post.uri,
          image_index: img.index,
          new_alt: draft
        });
      });
    });

    if (updates.length === 0) {
      setApplyMessage("No images selected for update.");
      return;
    }

    setApplying(true);
    try {
      const resp: ApplyResponse = await applyAltUpdates(handle, appPassword, updates);
      const successes = resp.updated.filter((r) => r.success).length;
      const failures = resp.updated.length - successes;

      setApplyMessage(
        `Applied alt text to ${successes} post(s).` +
          (failures > 0 ? ` ${failures} post(s) failed; check logs/errors.` : "")
      );

      // Optimistically update result alt fields for the UI
      const nextResult: ScanResponse = {
        ...result,
        posts: result.posts.map((post) => ({
          ...post,
          images: post.images.map((img) => {
            const key = makeKey(post.uri, img.index);
            const state = altState[key];
            if (state?.apply && state.draftAlt.trim()) {
              return {
                ...img,
                alt: state.draftAlt.trim()
              };
            }
            return img;
          })
        }))
      };
      setResult(nextResult);
    } catch (err: any) {
      console.error(err);
      let msg = "An error occurred while applying changes.";
      try {
        const parsed = JSON.parse(err.message);
        if (parsed?.detail) {
          msg =
            typeof parsed.detail === "string"
              ? parsed.detail
              : JSON.stringify(parsed.detail);
        }
      } catch {
        if (err.message) msg = err.message;
      }
      setError(msg);
    } finally {
      setApplying(false);
    }
  };

  const bulkSelectMissingAlt = () => {
    if (!result) return;
    setAltState((prev) => {
      const next: AltStateMap = { ...prev };
      result.posts.forEach((post) => {
        post.images.forEach((img) => {
          const key = makeKey(post.uri, img.index);
          const hasAlt = img.alt && img.alt.trim().length > 0;
          if (!hasAlt) {
            const existing = next[key] ?? {
              apply: false,
              draftAlt:
                img.alt && img.alt.trim().length > 0
                  ? img.alt
                  : img.generated_alt || ""
            };
            next[key] = {
              ...existing,
              apply: true
            };
          }
        });
      });
      return next;
    });
  };

  const bulkClearSelections = () => {
    setAltState((prev) => {
      const next: AltStateMap = {};
      for (const [key, value] of Object.entries(prev)) {
        next[key] = { ...value, apply: false };
      }
      return next;
    });
  };

  const shouldShowImage = (post: PostInfo, img: ImageInfo): boolean => {
    const key = makeKey(post.uri, img.index);
    const state = altState[key];
    const hasAlt = img.alt && img.alt.trim().length > 0;
    const isSelected = !!state?.apply;

    switch (filterMode) {
      case "missingAlt":
        return !hasAlt;
      case "hasAlt":
        return hasAlt;
      case "selected":
        return isSelected;
      case "all":
      default:
        return true;
    }
  };

  return (
    <div className="app-root">
      <div className="texture-overlay" />
      <header className="app-header">
        <h1>Bluesky Alt-Text Slinger</h1>
        <p className="subtitle">
          Phase 4: Scan, review suggestions, apply updates, with filters and SQLite persistence.
        </p>
      </header>

      <main className="app-main">
        <section className="card login-card">
          <h2>Connect to Bluesky</h2>
          <p className="card-help">
            Use your Bluesky handle and an <strong>app password</strong>, not your main password.
          </p>
          <form onSubmit={onSubmit} className="login-form">
            <label className="input-group">
              <span>Handle</span>
              <input
                type="text"
                placeholder="you.bsky.social"
                value={handle}
                onChange={(e) => setHandle(e.target.value)}
              />
            </label>

            <label className="input-group">
              <span>App Password</span>
              <input
                type="password"
                placeholder="xxxx-xxxx-xxxx-xxxx"
                value={appPassword}
                onChange={(e) => setAppPassword(e.target.value)}
              />
            </label>

            <button type="submit" className="primary-btn" disabled={loading}>
              {loading ? "Scanning..." : "Scan My Posts"}
            </button>

            {error && <div className="error-banner">{error}</div>}
          </form>
        </section>

        {result && (
          <section className="card results-card">
            <div className="results-header">
              <h2>Scan Results</h2>
              <p>
                Handle: <strong>{result.handle}</strong> · Posts with images:{" "}
                <strong>{result.total_posts}</strong> · Images:{" "}
                <strong>{result.total_images}</strong>
              </p>
              <p className="altgen-status">
                Alt-text generation:{" "}
                {result.alt_generation_enabled ? (
                  <span className="badge badge-on">Enabled</span>
                ) : (
                  <span className="badge badge-off">Disabled (no API key)</span>
                )}
              </p>
            </div>

            {applyMessage && <div className="apply-banner">{applyMessage}</div>}

            <div className="top-controls">
              <div className="apply-controls">
                <button
                  type="button"
                  className="primary-btn"
                  disabled={applying}
                  onClick={onApplyChanges}
                >
                  {applying ? "Applying..." : "Apply Selected Alt Text"}
                </button>
                <span className="apply-hint">
                  Only images with the checkbox enabled and non-empty alt text will be updated.
                </span>
              </div>

              <div className="filter-controls">
                <span className="filter-label">Filter:</span>
                <button
                  type="button"
                  className={`filter-btn ${filterMode === "all" ? "filter-btn-active" : ""}`}
                  onClick={() => setFilterMode("all")}
                >
                  All
                </button>
                <button
                  type="button"
                  className={`filter-btn ${
                    filterMode === "missingAlt" ? "filter-btn-active" : ""
                  }`}
                  onClick={() => setFilterMode("missingAlt")}
                >
                  Missing alt
                </button>
                <button
                  type="button"
                  className={`filter-btn ${
                    filterMode === "hasAlt" ? "filter-btn-active" : ""
                  }`}
                  onClick={() => setFilterMode("hasAlt")}
                >
                  Has alt
                </button>
                <button
                  type="button"
                  className={`filter-btn ${
                    filterMode === "selected" ? "filter-btn-active" : ""
                  }`}
                  onClick={() => setFilterMode("selected")}
                >
                  Selected
                </button>

                <span className="filter-divider" />

                <button
                  type="button"
                  className="filter-btn"
                  onClick={bulkSelectMissingAlt}
                >
                  Select all missing-alt
                </button>
                <button
                  type="button"
                  className="filter-btn"
                  onClick={bulkClearSelections}
                >
                  Clear selections
                </button>
              </div>
            </div>

            {result.total_images === 0 ? (
              <p>No images with embeds found in your posts.</p>
            ) : (
              <div className="images-grid">
                {result.posts.map((post: PostInfo) =>
                  post.images
                    .filter((img) => shouldShowImage(post, img))
                    .map((img: ImageInfo) => {
                      const key = makeKey(post.uri, img.index);
                      const state = altState[key] || {
                        apply: false,
                        draftAlt:
                          img.alt && img.alt.trim().length > 0
                            ? img.alt
                            : img.generated_alt || ""
                      };

                      return (
                        <article key={`${post.uri}-${img.index}`} className="image-card">
                          <div className="image-wrapper">
                            <img
                              src={img.thumb_url}
                              alt={img.alt || img.generated_alt || "Image thumbnail"}
                              className="image-thumb"
                            />
                          </div>
                          <div className="image-meta">
                            <div className="meta-row">
                              <span className="meta-label">Post text</span>
                              <span className="meta-value meta-text">
                                {post.text || <em>(no post text)</em>}
                              </span>
                            </div>
                            <div className="meta-row">
                              <span className="meta-label">Created</span>
                              <span className="meta-value">
                                {post.created_at ? (
                                  formatDate(post.created_at)
                                ) : (
                                  <em>unknown</em>
                                )}
                              </span>
                            </div>
                            <div className="meta-row">
                              <span className="meta-label">Existing alt text</span>
                              <span className="meta-value meta-alt">
                                {img.alt && img.alt.trim().length > 0 ? (
                                  img.alt
                                ) : (
                                  <em>(no alt text set)</em>
                                )}
                              </span>
                            </div>

                            <div className="meta-row">
                              <span className="meta-label">Suggested alt text</span>
                              <span className="meta-value meta-alt suggested-alt">
                                {img.generated_alt && img.generated_alt.trim().length > 0 ? (
                                  img.generated_alt
                                ) : result.alt_generation_enabled ? (
                                  <em>(no suggestion returned)</em>
                                ) : (
                                  <em>(configure OPENAI_API_KEY to enable suggestions)</em>
                                )}
                              </span>
                            </div>

                            <div className="meta-row">
                              <span className="meta-label">Alt text to apply</span>
                              <textarea
                                className="alt-textarea"
                                value={state.draftAlt}
                                onChange={(e) =>
                                  handleAltChange(post.uri, img.index, e.target.value)
                                }
                                rows={3}
                                placeholder="Type or refine alt text here"
                              />
                            </div>

                            <div className="meta-row apply-row">
                              <label className="apply-checkbox">
                                <input
                                  type="checkbox"
                                  checked={state.apply}
                                  onChange={(e) =>
                                    handleApplyToggle(
                                      post.uri,
                                      img.index,
                                      e.target.checked
                                    )
                                  }
                                />
                                <span>Apply this alt text to Bluesky</span>
                              </label>
                              <span className="meta-value">Image index #{img.index}</span>
                            </div>

                            <div className="meta-row link-row">
                              <a
                                href={post.uri}
                                target="_blank"
                                rel="noreferrer"
                                className="post-link"
                              >
                                View post (URI)
                              </a>
                              <a
                                href={img.fullsize_url}
                                target="_blank"
                                rel="noreferrer"
                                className="post-link"
                              >
                                Full-size image
                              </a>
                            </div>
                          </div>
                        </article>
                      );
                    })
                )}
              </div>
            )}
          </section>
        )}
      </main>

      <footer className="app-footer">
        <span>
          Phase 4 – changes you apply here update alt text on your Bluesky posts and are tracked
          in SQLite.
        </span>
      </footer>
    </div>
  );
};

export default App;