import React, { useEffect, useRef, useState } from "react";
import "./App.css";
import {
  scanImagesWithProgress,
  ScanResponse,
  PostInfo,
  ImageInfo,
  startApplyQueue,
  getApplyQueueState,
  pauseApplyQueue,
  resumeApplyQueue,
  AltUpdate,
  ApplyQueueStateResponse,
  ScanProgressEvent,
  startAltGeneration,
  pollAltGenerationEvents,
  stopAltGeneration,
  GenerateJobEvent,
  regenerateAltText, resetGenerationDrafts, discoverModels, ProviderModels, GenerationConfig
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
  userEdited: boolean;
};

type AltStateMap = {
  [key: string]: AltState;
};

function makeKey(uri: string, index: number): string {
  return `${uri}::${index}`;
}

function atUriToBskyWebUrl(uri: string, fallbackHandle?: string): string {
  if (!uri.startsWith("at://")) return uri;
  const parts = uri.slice(5).split("/");
  if (parts.length !== 3) return uri;
  const repo = parts[0];
  const collection = parts[1];
  const rkey = parts[2];
  if (collection !== "app.bsky.feed.post") return uri;
  const profile = repo.startsWith("did:") && fallbackHandle ? fallbackHandle : repo;
  return `https://bsky.app/profile/${profile}/post/${rkey}`;
}

type FilterMode = "all" | "missingAlt" | "hasAlt" | "selected";
type ScanStats = {
  postsScanned: number;
  imagesFound: number;
};

type GenStatus = "queued" | "generating" | "done" | "error";
type ApplyItemStatus = "idle" | "pending" | "propagating" | "running" | "applied" | "failed";
type CircleStatus = "black" | "red" | "darkgreen" | "green";
type ScanCircle = {
  uri: string;
  status: CircleStatus;
  missingPending: number;
};

const App: React.FC = () => {
  const [llmKey, setLlmKey] = useState("");
  const [providerChoice, setProviderChoice] = useState("auto");
  const [catalog, setCatalog] = useState<ProviderModels | null>(null);
  const [model, setModel] = useState("");
  const [modelsLoading, setModelsLoading] = useState(false);
  const [providerError, setProviderError] = useState("");
  const providerRequest = useRef(0);
  const generationConfig: GenerationConfig | undefined = catalog && model && llmKey.trim()
    ? {api_key: llmKey.trim(), provider: catalog.provider, model} : undefined;
  const invalidateProvider = () => {
    providerRequest.current += 1;
    setCatalog(null); setModel(""); setProviderError(""); setModelsLoading(false);
  };
  const loadModels = async () => {
    const request = ++providerRequest.current;
    setModelsLoading(true); setProviderError(""); setCatalog(null); setModel("");
    try {
      const data = await discoverModels(llmKey.trim(), providerChoice);
      if (providerRequest.current !== request) return;
      setCatalog(data);
      setModel(data.models[0]?.id || "");
      if (!data.models.length) setProviderError("No supported image-description models were returned for this provider.");
    } catch (err) {
      if (providerRequest.current === request) setProviderError(err instanceof Error ? err.message : "Unable to load models.");
    } finally {
      if (providerRequest.current === request) setModelsLoading(false);
    }
  };
  const [handle, setHandle] = useState("");
  const [appPassword, setAppPassword] = useState("");
  const [resettingDrafts, setResettingDrafts] = useState(false);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applyMessage, setApplyMessage] = useState<string | null>(null);
  const [result, setResult] = useState<ScanResponse | null>(null);
  const [altState, setAltState] = useState<AltStateMap>({});
  const [filterMode, setFilterMode] = useState<FilterMode>("all");
  const [scanLogLines, setScanLogLines] = useState<string[]>([]);
  const [scanCircles, setScanCircles] = useState<ScanCircle[]>([]);
  const [scanStats, setScanStats] = useState<ScanStats>({
    postsScanned: 0,
    imagesFound: 0
  });
  const [generationJobId, setGenerationJobId] = useState<string | null>(null);
  const [generationRunning, setGenerationRunning] = useState(false);
  const [generationStopping, setGenerationStopping] = useState(false);
  const [generationProcessed, setGenerationProcessed] = useState(0);
  const [generationTotal, setGenerationTotal] = useState(0);
  const [applyProcessed, setApplyProcessed] = useState(0);
  const [applyTotal, setApplyTotal] = useState(0);
  const [applyJobId, setApplyJobId] = useState<string | null>(null);
  const [applyQueueState, setApplyQueueState] = useState<ApplyQueueStateResponse | null>(null);
  const [applyItemStatus, setApplyItemStatus] = useState<Record<string, ApplyItemStatus>>({});
  const [imageGenStatus, setImageGenStatus] = useState<Record<string, GenStatus>>({});
  const [imageGenError, setImageGenError] = useState<Record<string, string>>({});
  const regenerationInFlight = useRef(false);
  const [regeneratingKeyMap, setRegeneratingKeyMap] = useState<Record<string, boolean>>({});
  const [activeGenerationUri, setActiveGenerationUri] = useState<string | null>(null);
  const [activeApplyUri, setActiveApplyUri] = useState<string | null>(null);
  const lastGenSeqRef = useRef(0);
  const pollTimerRef = useRef<number | null>(null);
  const applyPollTimerRef = useRef<number | null>(null);
  const altStateRef = useRef<AltStateMap>({});
  const scanMapRef = useRef<HTMLDivElement | null>(null);
  const dotRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  useEffect(() => {
    altStateRef.current = altState;
  }, [altState]);

  useEffect(() => {
    const activeUri = activeApplyUri || activeGenerationUri;
    if (!activeUri) return;
    const container = scanMapRef.current;
    const el = dotRefs.current[activeUri];
    if (!container || !el) return;
    const cRect = container.getBoundingClientRect();
    const eRect = el.getBoundingClientRect();
    const margin = 12;
    const outVertical = eRect.top < cRect.top + margin || eRect.bottom > cRect.bottom - margin;
    const outHorizontal = eRect.left < cRect.left + margin || eRect.right > cRect.right - margin;
    if (outVertical || outHorizontal) {
      const targetTop = el.offsetTop - (container.clientHeight - el.offsetHeight) / 2;
      const targetLeft = el.offsetLeft - (container.clientWidth - el.offsetWidth) / 2;
      const maxTop = Math.max(0, container.scrollHeight - container.clientHeight);
      const maxLeft = Math.max(0, container.scrollWidth - container.clientWidth);
      container.scrollTo({
        top: outVertical ? Math.max(0, Math.min(maxTop, targetTop)) : container.scrollTop,
        left: outHorizontal ? Math.max(0, Math.min(maxLeft, targetLeft)) : container.scrollLeft,
        behavior: "smooth"
      });
    }
  }, [activeGenerationUri, activeApplyUri, scanCircles]);

  useEffect(() => {
    if (!generationJobId || !generationRunning) return;
    let inFlight = false;
    let cancelled = false;
    const poll = async () => {
      if (inFlight || cancelled) return;
      inFlight = true;
      try {
        const data = await pollAltGenerationEvents(generationJobId, lastGenSeqRef.current);
        if (cancelled) return;
        if (typeof data.processed_items === "number") {
          setGenerationProcessed(data.processed_items);
        }
        if (typeof data.total_items === "number") {
          setGenerationTotal(data.total_items);
        }
        if (data.events.length > 0) {
          for (const event of data.events) {
            if (event.seq <= lastGenSeqRef.current) continue;
            lastGenSeqRef.current = event.seq;
            handleGenerationEvent(event);
          }
        }
        if (data.done) {
          setGenerationRunning(false);
          setGenerationStopping(false);
          if (pollTimerRef.current) {
            window.clearInterval(pollTimerRef.current);
            pollTimerRef.current = null;
          }
        }
      } catch (err: any) {
        appendScanLog(`Generation polling error: ${err?.message || "unknown error"}`);
        // Keep the batch active and retry; a failed poll does not stop the worker.
      } finally {
        inFlight = false;
      }
    };

    poll();
    pollTimerRef.current = window.setInterval(poll, 700);
    return () => {
      cancelled = true;
      if (pollTimerRef.current) {
        window.clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [generationJobId, generationRunning]);

  useEffect(() => {
    if (!applyJobId) return;
    let inFlight = false;
    let cancelled = false;

    const poll = async () => {
      if (inFlight || cancelled) return;
      inFlight = true;
      try {
        const state = await getApplyQueueState(applyJobId);
        if (cancelled) return;
        setApplyQueueState(state);
        setApplying(state.status === "running");
        setApplyProcessed(state.processed_items);
        setApplyTotal(state.total_items);
        setActiveApplyUri(state.active_uri || null);

        const nextItemStatus: Record<string, ApplyItemStatus> = {};
        state.items.forEach((item) => {
          const key = makeKey(item.uri, item.image_index);
          const err = (item.error || "").toLowerCase();
          if (
            (item.status === "propagating") ||
            (
              item.status === "pending" &&
            (err.includes("propagation") || err.includes("pds accepted"))
            )
          ) {
            nextItemStatus[key] = "propagating";
          } else {
            nextItemStatus[key] = item.status as ApplyItemStatus;
          }
        });
        setApplyItemStatus((prev) => ({ ...prev, ...nextItemStatus }));

        const successKeys = new Set(
          state.items
            .filter((x) => x.status === "applied")
            .map((x) => makeKey(x.uri, x.image_index))
        );
        const failedUris = new Set(
          state.items.filter((x) => x.status === "failed").map((x) => x.uri)
        );
        const runningUris = new Set(
          state.items.filter((x) => x.status === "running").map((x) => x.uri)
        );
        const pendingUris = new Set(
          state.items.filter((x) => x.status === "pending").map((x) => x.uri)
        );
        const propagatingUris = new Set(
          state.items.filter((x) => x.status === "propagating").map((x) => x.uri)
        );

        setResult((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            posts: prev.posts.map((post) => ({
              ...post,
              images: post.images.map((img) => {
                const key = makeKey(post.uri, img.index);
                const draft = altStateRef.current[key]?.draftAlt?.trim();
                if (successKeys.has(key) && draft) {
                  return { ...img, alt: draft };
                }
                return img;
              })
            }))
          };
        });

        setScanCircles((prev) =>
          prev.map((circle) => {
            if (circle.status === "black") return circle;
            if (failedUris.has(circle.uri)) return { ...circle, status: "darkgreen" };
            if (runningUris.has(circle.uri)) return circle;
            if (propagatingUris.has(circle.uri)) return { ...circle, status: "darkgreen" };
            if (pendingUris.has(circle.uri)) return { ...circle, status: "darkgreen" };
            const uriItems = state.items.filter((x) => x.uri === circle.uri);
            if (uriItems.length > 0 && uriItems.every((x) => x.status === "applied")) {
              return { ...circle, status: "green", missingPending: 0 };
            }
            return circle;
          })
        );

        if (state.status === "completed") {
          setApplying(false);
          setActiveApplyUri(null);
          if (applyPollTimerRef.current) {
            window.clearInterval(applyPollTimerRef.current);
            applyPollTimerRef.current = null;
          }
          const errMsgs = state.items
            .filter((x) => x.status === "failed" && x.error)
            .map((x) => x.error as string);
          const uniqErr = Array.from(new Set(errMsgs)).slice(0, 3);
          setApplyMessage(
            `Applied alt text to ${state.success_items} image(s).` +
              (state.failed_items > 0
                ? ` ${state.failed_items} image(s) failed.` +
                  (uniqErr.length > 0 ? ` Errors: ${uniqErr.join(" | ")}` : "")
                : "")
          );
        } else if (state.status === "paused") {
          setApplying(false);
          if (state.pause_reason) {
            setApplyMessage(
              `Apply queue paused. ${state.pause_reason}` +
                (state.rate_limit_reset_at
                  ? ` Retry after ${new Date(state.rate_limit_reset_at * 1000).toLocaleString()}.`
                  : "")
            );
          }
        } else if (state.status === "running" && state.failed_items > 0) {
          const firstErr = state.items.find((x) => x.status === "failed" && x.error)?.error;
          setApplyMessage(
            `Apply queue running: ${state.success_items} applied, ${state.failed_items} failed so far.` +
              (firstErr ? ` Latest error: ${firstErr}` : "")
          );
        }
      } catch (err: any) {
        appendScanLog(`Apply queue polling error: ${err?.message || "unknown error"}`);
        // Keep polling: a transient network failure must not freeze progress.
      } finally {
        inFlight = false;
      }
    };

    poll();
    applyPollTimerRef.current = window.setInterval(poll, 1000);
    return () => {
      cancelled = true;
      if (applyPollTimerRef.current) {
        window.clearInterval(applyPollTimerRef.current);
        applyPollTimerRef.current = null;
      }
    };
  }, [applyJobId]);

  const appendScanLog = (line: string) => {
    const timestamp = new Date().toLocaleTimeString();
    setScanLogLines((prev) => [...prev.slice(-399), `[${timestamp}] ${line}`]);
  };

  const resetGenerationState = () => {
    setGenerationJobId(null);
    setGenerationRunning(false);
    setGenerationStopping(false);
    setGenerationProcessed(0);
    setGenerationTotal(0);
    setImageGenStatus({});
    setImageGenError({});
    setRegeneratingKeyMap({});
    setScanCircles([]);
    setActiveGenerationUri(null);
    setActiveApplyUri(null);
    setApplyProcessed(0);
    setApplyTotal(0);
    setApplyJobId(null);
    setApplyQueueState(null);
    setApplyItemStatus({});
    lastGenSeqRef.current = 0;
    if (pollTimerRef.current) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    if (applyPollTimerRef.current) {
      window.clearInterval(applyPollTimerRef.current);
      applyPollTimerRef.current = null;
    }
  };

  const applyGeneratedAlt = (uri: string, index: number, generatedAlt: string, replaceSuggestion = false) => {
    setResult((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        posts: prev.posts.map((post) => {
          if (post.uri !== uri) return post;
          return {
            ...post,
            images: post.images.map((img) =>
              img.index === index ? { ...img, generated_alt: generatedAlt } : img
            )
          };
        })
      };
    });

    const key = makeKey(uri, index);
    setAltState((prev) => {
      const current = prev[key];
      if (!current) return prev;
      if (current.userEdited) return prev;
      if (!replaceSuggestion && current.draftAlt && current.draftAlt.trim().length > 0) return prev;
      return {
        ...prev,
        [key]: { ...current, draftAlt: generatedAlt }
      };
    });
  };

  const handleGenerationEvent = (event: GenerateJobEvent) => {
    if (event.type === "started" && event.message) {
      appendScanLog(event.message);
    }
    if (event.type === "item_started" && event.uri && typeof event.image_index === "number") {
      const key = makeKey(event.uri, event.image_index);
      setImageGenStatus((prev) => ({ ...prev, [key]: "generating" }));
      setActiveGenerationUri(event.uri);
    }
    if (event.type === "item_result" && event.uri && typeof event.image_index === "number") {
      const key = makeKey(event.uri, event.image_index);
      if (event.error) {
        setImageGenStatus((prev) => ({ ...prev, [key]: "error" }));
        setImageGenError((prev) => ({ ...prev, [key]: event.error || "Generation failed." }));
      } else {
        setImageGenStatus((prev) => ({ ...prev, [key]: "done" }));
        if (event.generated_alt && event.generated_alt.trim().length > 0) {
          applyGeneratedAlt(event.uri, event.image_index, event.generated_alt);
          setScanCircles((prev) =>
            prev.map((circle) => {
              if (circle.uri !== event.uri) return circle;
              const nextPending = Math.max(0, circle.missingPending - 1);
              return {
                ...circle,
                missingPending: nextPending,
                status: nextPending === 0 ? "darkgreen" : circle.status
              };
            })
          );
        }
      }
    }
    if (event.type === "stop_requested" && event.message) {
      appendScanLog(event.message);
    }
    if (event.type === "complete") {
      const stopped = !!event.stop_requested;
      appendScanLog(
        stopped
          ? `Generation stopped. Completed ${event.processed_items ?? 0}/${event.total_items ?? 0}.`
          : `Generation complete. Completed ${event.processed_items ?? 0}/${event.total_items ?? 0}.`
      );
      setGenerationRunning(false);
      setGenerationStopping(false);
      setActiveGenerationUri(null);
    }
  };

  const initAltStateFromResult = (data: ScanResponse) => {
    const next: AltStateMap = {};
    const nextApplyStatus: Record<string, ApplyItemStatus> = {};
    data.posts.forEach((post) => {
      post.images.forEach((img) => {
        const key = makeKey(post.uri, img.index);
        const baseAlt =
          img.alt && img.alt.trim().length > 0 ? img.alt : img.generated_alt || "";
        next[key] = {
          apply: !img.alt || img.alt.trim().length === 0, // default: auto-select only images with no existing alt
          draftAlt: baseAlt,
          userEdited: false
        };
        if (img.apply_status === "applied") {
          nextApplyStatus[key] = "applied";
        } else if (img.apply_status === "failed") {
          nextApplyStatus[key] = "failed";
        } else {
          nextApplyStatus[key] = "idle";
        }
      });
    });
    setAltState(next);
    setApplyItemStatus(nextApplyStatus);
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (resettingDrafts || generationRunning || generationStopping || regenerationInFlight.current || loading) return;
    setError(null);
    setApplyMessage(null);
    setResult(null);
    setAltState({});
    setScanLogLines([]);
    setScanStats({ postsScanned: 0, imagesFound: 0 });
    resetGenerationState();

    if (!handle || !appPassword) {
      setError("Please enter both handle and app password.");
      return;
    }

    if (llmKey.trim() && !generationConfig) {
      setError("Load models and choose a model before scanning, or clear the LLM key to scan without it.");
      return;
    }
    setLoading(true);
    try {
      appendScanLog("Starting scan...");
      const data = await scanImagesWithProgress(
        {
          handle,
          app_password: appPassword,
          generate_alt: false
        },
        (event: ScanProgressEvent) => {
          if (event.event === "post_scanned" && event.post_uri && event.post_state) {
            const nextStatus: CircleStatus =
              event.post_state === "no_images"
                ? "black"
                : event.post_state === "images_missing_alt"
                  ? "red"
                  : event.post_state === "images_generated_not_applied"
                    ? "darkgreen"
                  : "green";
            const missingPending = event.missing_images_needing_generation ?? 0;
            setScanCircles((prev) => {
              const idx = prev.findIndex((c) => c.uri === event.post_uri);
              if (idx === -1) {
                return [
                  ...prev,
                  { uri: event.post_uri as string, status: nextStatus, missingPending }
                ];
              }
              const next = [...prev];
              next[idx] = { ...next[idx], status: nextStatus, missingPending };
              return next;
            });
          }
          if (event.type === "progress" && event.message) {
            appendScanLog(event.message);
            if (
              typeof event.posts_scanned === "number" ||
              typeof event.images_found === "number"
            ) {
              setScanStats((prev) => ({
                postsScanned: event.posts_scanned ?? prev.postsScanned,
                imagesFound: event.images_found ?? prev.imagesFound
              }));
            }
          }
        }
      );
      appendScanLog(
        `Scan complete: ${data.total_posts} posts with images, ${data.total_images} images found.`
      );
      setResult(data);
      initAltStateFromResult(data);

      const generationItems = data.posts.flatMap((post) =>
        post.images
          .filter(
            (img) =>
              (!img.alt || img.alt.trim().length === 0) &&
              (!img.generated_alt || img.generated_alt.trim().length === 0)
          )
          .map((img) => ({
            uri: post.uri,
            image_index: img.index,
            fullsize_url: img.fullsize_url,
            post_text: post.text || "",
            current_alt: img.alt
          }))
      );

      if (!data.alt_generation_enabled && !generationConfig) {
        appendScanLog("Alt generation disabled. Add an LLM key above to enable it.");
      } else if (generationItems.length === 0) {
        appendScanLog("No missing-alt images found. Nothing to generate.");
      } else {
        setGenerationTotal(generationItems.length);
        setGenerationProcessed(0);
        const queued: Record<string, GenStatus> = {};
        generationItems.forEach((item) => {
          queued[makeKey(item.uri, item.image_index)] = "queued";
        });
        setImageGenStatus(queued);
        appendScanLog(`Starting alt-text generation for ${generationItems.length} images...`);

        const started = await startAltGeneration(handle, generationItems, generationConfig);
        setGenerationJobId(started.job_id);
        setGenerationRunning(true);
      }
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
      appendScanLog(`Scan failed: ${msg}`);
    } finally {
      setLoading(false);
    }
  };

  const onDiscardAndRegenerate = async () => {
    if (!result || loading || resettingDrafts || generationRunning || generationStopping || regenerationInFlight.current ||
        (applyQueueState && applyQueueState.status !== "completed")) return;
    if ((!generationConfig && !result.alt_generation_enabled) || (llmKey.trim() && !generationConfig)) {
      setError("Connect an AI provider and select a model before starting a fresh run.");
      return;
    }
    setResettingDrafts(true);
    setError(null);
    let cleared = false;
    try {
      const items = result.posts.flatMap(post => post.images.map(img => ({
        uri: post.uri, image_index: img.index, fullsize_url: img.fullsize_url,
        post_text: post.text || "", current_alt: img.alt
      })));
      const saved = await resetGenerationDrafts(result.handle, appPassword, items);
      const savedMap = new Map(saved.images.map(img => [makeKey(img.uri, img.image_index), img.alt]));
      const clean = {...result, posts: result.posts.map(post => ({...post, images: post.images.map(img => ({
        ...img, alt: savedMap.get(makeKey(post.uri, img.index)) || "", generated_alt: null, apply_status: null
      }))}))};
      cleared = true;
      setResult(clean);
      initAltStateFromResult(clean);
      setImageGenError({}); setImageGenStatus({});
      setApplyJobId(null); setApplyQueueState(null); setApplyItemStatus({});
      setApplyProcessed(0); setApplyTotal(0); setActiveApplyUri(null);
      setGenerationJobId(null); lastGenSeqRef.current = 0;
      setGenerationProcessed(0);
      const pending = items.filter(item => !(savedMap.get(makeKey(item.uri, item.image_index)) || "").trim())
        .map(item => ({...item, current_alt: ""}));
      setGenerationTotal(pending.length);
      setScanCircles(prev => prev.map(circle => {
        const post = clean.posts.find(p => p.uri === circle.uri);
        if (!post) return circle;
        const missing = post.images.filter(img => !img.alt.trim()).length;
        return {...circle, missingPending: missing, status: missing ? "red" : "green"};
      }));
      setApplyMessage("Unpublished drafts discarded. Alt text saved to Bluesky was preserved.");
      if (pending.length) {
        const started = await startAltGeneration(result.handle, pending, generationConfig);
        setImageGenStatus(Object.fromEntries(pending.map(item => [makeKey(item.uri, item.image_index), "queued"])));
        setGenerationJobId(started.job_id); setGenerationRunning(true);
      }
    } catch (err) {
      setError(`${cleared ? "Drafts were cleared, but the new run could not start. " : ""}${err instanceof Error ? err.message : "Unable to restart generation."}`);
    } finally {
      setResettingDrafts(false);
    }
  };

  const onStopGeneration = async () => {
    if (!generationJobId || !generationRunning || generationStopping) return;
    setGenerationStopping(true);
    appendScanLog("Requesting generation stop...");
    try {
      await stopAltGeneration(generationJobId);
    } catch (err: any) {
      setGenerationStopping(false);
      appendScanLog(`Failed to stop generation: ${err?.message || "unknown error"}`);
    }
  };

  const onRegenerateAlt = async (post: PostInfo, img: ImageInfo) => {
    if (resettingDrafts || generationRunning || generationStopping || loading || regenerationInFlight.current) return;
    if (llmKey.trim() && !generationConfig) {
      setProviderError("Load models and choose a model before regenerating.");
      return;
    }
    const key = makeKey(post.uri, img.index);
    regenerationInFlight.current = true;
    setRegeneratingKeyMap((prev) => ({ ...prev, [key]: true }));
    setImageGenStatus((prev) => ({ ...prev, [key]: "generating" }));
    setActiveGenerationUri(post.uri);
    setImageGenError((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
    appendScanLog(`Regenerating alt text for ${post.uri} image #${img.index}...`);

    try {
      const resp = await regenerateAltText(handle, {
        uri: post.uri,
        image_index: img.index,
        fullsize_url: img.fullsize_url,
        post_text: post.text || "",
        current_alt: img.alt
      }, generationConfig);

      if (resp.generated_alt && resp.generated_alt.trim().length > 0) {
        applyGeneratedAlt(post.uri, img.index, resp.generated_alt.trim(), true);
        setImageGenStatus((prev) => ({ ...prev, [key]: "done" }));
        setScanCircles((prev) =>
          prev.map((circle) => {
            if (circle.uri !== post.uri) return circle;
            const nextPending = Math.max(0, circle.missingPending - 1);
            return {
              ...circle,
              missingPending: nextPending,
              status: nextPending === 0 ? "darkgreen" : circle.status
            };
          })
        );
        appendScanLog(`Regenerated alt text for ${post.uri} image #${img.index}.`);
      } else {
        const err = resp.error || "No text returned.";
        setImageGenStatus((prev) => ({ ...prev, [key]: "error" }));
        setImageGenError((prev) => ({ ...prev, [key]: err }));
        appendScanLog(`Regenerate failed for ${post.uri} image #${img.index}: ${err}`);
      }
    } catch (err: any) {
      const msg = err?.message || "Unknown error";
      setImageGenStatus((prev) => ({ ...prev, [key]: "error" }));
      setImageGenError((prev) => ({ ...prev, [key]: msg }));
      appendScanLog(`Regenerate failed for ${post.uri} image #${img.index}: ${msg}`);
    } finally {
      regenerationInFlight.current = false;
      setRegeneratingKeyMap((prev) => ({ ...prev, [key]: false }));
      setActiveGenerationUri((prev) => (prev === post.uri ? null : prev));
    }
  };

  const handleAltChange = (uri: string, index: number, value: string) => {
    const key = makeKey(uri, index);
    setAltState((prev) => ({
      ...prev,
      [key]: {
        apply: prev[key]?.apply ?? false,
        draftAlt: value,
        userEdited: true
      }
    }));
  };

  const handleApplyToggle = (uri: string, index: number, apply: boolean) => {
    const key = makeKey(uri, index);
    setAltState((prev) => ({
      ...prev,
      [key]: {
        apply,
        draftAlt: prev[key]?.draftAlt ?? "",
        userEdited: prev[key]?.userEdited ?? false
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
    setApplyTotal(updates.length);
    setApplyProcessed(0);
    try {
      const started = await startApplyQueue(handle, appPassword, updates);
      setApplyJobId(started.job_id);
      setApplyMessage(
        `Apply queue started for ${started.total_items} image(s). Running with rate-limit-aware pacing.`
      );
      const nextStatuses: Record<string, ApplyItemStatus> = {};
      updates.forEach((u) => {
        nextStatuses[makeKey(u.uri, u.image_index)] = "pending";
      });
      setApplyItemStatus((prev) => ({ ...prev, ...nextStatuses }));
      appendScanLog(`Apply queue started: ${started.job_id}`);
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
      setApplying(false);
    } finally {
      setActiveApplyUri(null);
    }
  };

  const onPauseApplyQueue = async () => {
    if (!applyJobId) return;
    try {
      await pauseApplyQueue(applyJobId);
      setApplyMessage("Apply queue paused.");
    } catch (err: any) {
      setError(err?.message || "Failed to pause apply queue.");
    }
  };

  const onResumeApplyQueue = async () => {
    if (!applyJobId) return;
    try {
      await resumeApplyQueue(applyJobId, handle, appPassword);
      setApplying(true);
      setApplyMessage("Apply queue resumed.");
    } catch (err: any) {
      setError(err?.message || "Failed to resume apply queue.");
    }
  };

  const bulkSelectMissingAlt = () => {
    if (!result) return;
    setAltState((prev) => {
      const next: AltStateMap = { ...prev };
      result.posts.forEach((post) => {
        post.images.forEach((img) => {
          const key = makeKey(post.uri, img.index);
          const hasAlt = !!img.alt && img.alt.trim().length > 0;
          if (!hasAlt) {
            const existing = next[key] ?? {
              apply: false,
              draftAlt:
                img.alt && img.alt.trim().length > 0
                  ? img.alt
                  : img.generated_alt || "",
              userEdited: false
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
    const hasAlt = !!img.alt && img.alt.trim().length > 0;
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

  const getApplyStatusLabel = (status: ApplyItemStatus): string => {
    switch (status) {
      case "pending":
        return applyQueueState?.status === "paused" ? "queued (paused)" : "queued";
      case "propagating":
        return "propagating to Bluesky";
      case "running":
        return "applying now";
      case "applied":
        return "applied";
      case "failed":
        return "failed";
      case "idle":
      default:
        return "not queued";
    }
  };

  const getPostQueueSummary = (uri: string): string => {
    if (!applyQueueState) return "Queue: none";
    const rows = applyQueueState.items.filter((x) => x.uri === uri);
    if (rows.length === 0) return "Queue: none";
    const propagating = rows.filter(
      (x) =>
        (x.status === "propagating") ||
        (
          x.status === "pending" &&
        ((x.error || "").toLowerCase().includes("propagation") ||
          (x.error || "").toLowerCase().includes("pds accepted"))
        )
    ).length;
    const running = rows.filter((x) => x.status === "running").length;
    const pending = rows.filter((x) => x.status === "pending").length - propagating;
    const failed = rows.filter((x) => x.status === "failed").length;
    const applied = rows.filter((x) => x.status === "applied").length;
    return `Queue ${applyQueueState.status}: running ${running}, propagating ${propagating}, pending ${Math.max(0, pending)}, applied ${applied}, failed ${failed}`;
  };

  const isPostGenerating = (uri: string): boolean => {
    const prefix = `${uri}::`;
    return Object.entries(imageGenStatus).some(
      ([key, status]) => key.startsWith(prefix) && status === "generating"
    );
  };

  const isPostActive = (uri: string): boolean => {
    if (activeApplyUri === uri) return true;
    return isPostGenerating(uri);
  };

  const getPostQueueClass = (uri: string): string => {
    if (!applyQueueState) return "";
    const rows = applyQueueState.items.filter((x) => x.uri === uri);
    if (rows.length === 0) return "";
    if (rows.some((x) => x.status === "running")) return "queue-running";
    if (
      rows.some(
        (x) =>
          x.status === "propagating" ||
          (
            x.status === "pending" &&
          ((x.error || "").toLowerCase().includes("propagation") ||
            (x.error || "").toLowerCase().includes("pds accepted"))
          )
      )
    ) {
      return "queue-propagating";
    }
    if (rows.some((x) => x.status === "pending")) {
      return applyQueueState.status === "paused" ? "queue-paused" : "queue-pending";
    }
    return "";
  };

  return (
    <div className="app-root">
      <div className="texture-overlay" />
      <header className="app-header">
        <h1>Alt Text Slinger</h1>
        <p className="subtitle">
          Scan posts, generate alt text, and apply updates with live visual progress.
        </p>
      </header>

      <main className="app-main">
        <section className="card login-card">
          <h2>Connect to Bluesky</h2>
          <p className="card-help">
            Use your Bluesky handle and an <strong>app password</strong>, not your main password.
          </p>
          <p className="key-privacy-note">Your app password stays in memory and is sent via the local backend only to Bluesky. It is never saved to disk or browser storage.</p>
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
                autoComplete="off"
                placeholder="xxxx-xxxx-xxxx-xxxx"
                value={appPassword}
                onChange={(e) => setAppPassword(e.target.value)}
              />
            </label>

            <button type="submit" className="primary-btn" disabled={resettingDrafts || loading || generationRunning || Object.values(regeneratingKeyMap).some(Boolean)}>
              {loading
                ? `Scanning... (${scanStats.postsScanned} posts, ${scanStats.imagesFound} images)`
                : "Scan My Posts"}
            </button>

            {error && <div className="error-banner">{error}</div>}
          </form>

          {(loading || generationRunning || scanCircles.length > 0) && (
            <div className="scan-progress-panel">
              <div className="scan-progress-header">
                <strong>Scan/generation map</strong>
                <span>
                  Posts scanned: {scanStats.postsScanned} · Images found: {scanStats.imagesFound}
                  {generationTotal > 0
                    ? ` · Generated: ${generationProcessed}/${generationTotal}`
                    : ""}
                  {applyTotal > 0 ? ` · Applied: ${applyProcessed}/${applyTotal}` : ""}
                </span>
                {generationRunning && (
                  <button
                    type="button"
                    className="stop-btn"
                    onClick={onStopGeneration}
                    disabled={generationStopping}
                  >
                    {generationStopping ? "Stopping..." : "Stop Generation"}
                  </button>
                )}
              </div>
              <div className="scan-dot-legend">
                <span><i className="scan-dot black" /> No images</span>
                <span><i className="scan-dot red" /> Images missing alt</span>
                <span><i className="scan-dot darkgreen" /> Generated (not applied)</span>
                <span><i className="scan-dot green" /> Applied/ready</span>
                <span><i className="scan-dot queue-propagating" /> Propagating</span>
                <span><i className="scan-dot queue-pending" /> In apply queue</span>
                <span><i className="scan-dot queue-paused" /> Queue paused</span>
              </div>
              <div className="scan-dot-grid" ref={scanMapRef}>
                {scanCircles.length === 0 ? (
                  <div className="scan-log-line">Preparing scan...</div>
                ) : (
                  scanCircles.map((circle, idx) => (
                    (() => {
                      const active = isPostActive(circle.uri);
                      const statusLabel =
                        circle.status === "black"
                          ? "No images"
                          : circle.status === "red"
                            ? "Images missing alt"
                            : circle.status === "darkgreen"
                              ? "Generated locally (not fully applied)"
                              : "Applied/ready";
                      const pendingLabel =
                        circle.missingPending > 0
                          ? `Missing images pending generation: ${circle.missingPending}`
                          : "Missing images pending generation: 0";
                      const queueLabel = getPostQueueSummary(circle.uri);
                      const tooltip = `${statusLabel}\n${pendingLabel}\n${queueLabel}\n${circle.uri}`;
                      return (
                        <button
                          type="button"
                          key={`${circle.uri}-${idx}`}
                          onClick={() =>
                            window.open(
                              atUriToBskyWebUrl(circle.uri, result?.handle || handle),
                              "_blank",
                              "noopener,noreferrer"
                            )
                          }
                          ref={(el) => {
                            dotRefs.current[circle.uri] = el;
                          }}
                          className={`scan-dot ${circle.status} ${active ? "active" : ""} ${getPostQueueClass(circle.uri)}`}
                          title={tooltip}
                          aria-label={tooltip}
                        />
                      );
                    })()
                  ))
                )}
              </div>
            </div>
          )}
        </section>

        <details className="card provider-card">
          <summary>AI Provider <span className="provider-summary-hint">Optional</span></summary>
          <p className="card-help">Optional: connect OpenAI or OpenRouter to generate image descriptions.</p>
          <div className="login-form">
            <label className="input-group">
              <span>LLM API key</span>
              <input type="password" autoComplete="off" spellCheck={false}
                placeholder="Paste your provider API key" value={llmKey}
                onChange={e => { invalidateProvider(); setLlmKey(e.target.value); }} />
            </label>
            <label className="input-group">
              <span>Provider</span>
              <select value={providerChoice} onChange={e => { invalidateProvider(); setProviderChoice(e.target.value); }}>
                <option value="auto">Detect from key</option>
                <option value="openai">OpenAI</option>
                <option value="openrouter">OpenRouter</option>
              </select>
            </label>
            <p className="card-help">{llmKey.trim().startsWith("sk-or-") ? "Detected: OpenRouter" :
              /^(sk-proj-|sk-svcacct-)/.test(llmKey.trim()) ? "Detected: OpenAI" :
              "Keys without a recognizable prefix require a provider selection."}</p>
            <button type="button" className="primary-btn" disabled={!llmKey.trim() || modelsLoading} onClick={loadModels}>
              {modelsLoading ? "Loading models…" : "Load models"}
            </button>
            {catalog && <label className="input-group">
              <span>Model ({catalog.provider === "openai" ? "OpenAI" : "OpenRouter"})</span>
              <select value={model} onChange={e => setModel(e.target.value)} disabled={!catalog.models.length}>
                {!catalog.models.length && <option value="">No compatible models</option>}
                {catalog.models.map(m => <option key={m.id} value={m.id}>{m.name} — {m.id}</option>)}
              </select>
            </label>}
            {catalog && <p className="card-help">{catalog.note}</p>}
            {providerError && <div className="error-banner" role="alert">{providerError}</div>}
            {llmKey && <button type="button" className="filter-btn" onClick={() => { invalidateProvider(); setLlmKey(""); }}>Clear API key</button>}
          </div>
          <p className="key-privacy-note">Your key stays in memory and is sent via the local backend only to your AI provider. It is never saved to disk or browser storage.</p>
        </details>

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
                {(generationConfig || result.alt_generation_enabled) ? (
                  <span className="badge badge-on">Enabled</span>
                ) : (
                  <span className="badge badge-off">Disabled (no API key)</span>
                )}
              </p>
            </div>

            {applyMessage && <div className="apply-banner">{applyMessage}</div>}

            <div className="top-controls">
              <div className="apply-controls">
                <button type="button" className="filter-btn" onClick={onDiscardAndRegenerate}
                  disabled={resettingDrafts || loading || generationRunning || generationStopping || Object.values(regeneratingKeyMap).some(Boolean) || !!(applyQueueState && applyQueueState.status !== "completed")}>
                  {resettingDrafts ? "Checking saved alt text…" : "Discard drafts and regenerate"}
                </button>
                <span className="apply-hint">Discards all unpublished suggestions and manual edits in these results. Preserves saved Bluesky alt text. Finish or stop generation and finish any apply queue first.</span>
              </div>
              <div className="apply-controls">
                <button
                  type="button"
                  className="primary-btn"
                  disabled={resettingDrafts || (applying && applyQueueState?.status === "running")}
                  onClick={onApplyChanges}
                >
                  {applying ? "Queue Running..." : "Apply Selected Alt Text"}
                </button>
                {applyJobId && applyQueueState?.status === "running" && (
                  <button type="button" className="filter-btn" onClick={onPauseApplyQueue}>
                    Pause Queue
                  </button>
                )}
                {applyJobId && applyQueueState?.status === "paused" && (
                  <button type="button" className="filter-btn" onClick={onResumeApplyQueue}>
                    Resume Queue
                  </button>
                )}
                <span className="apply-hint">
                  Only checked images with non-empty alt text are queued.
                </span>
              </div>
              {applyQueueState && (
                <p className="apply-hint">
                  Queue: <strong>{applyQueueState.status}</strong> · Done{" "}
                  {applyQueueState.processed_items}/{applyQueueState.total_items} · Success{" "}
                  {applyQueueState.success_items} · Failed {applyQueueState.failed_items} · Propagating{" "}
                  {applyQueueState.propagating_items} · Pending{" "}
                  {applyQueueState.pending_items} · Running {applyQueueState.running_items}
                  {applyQueueState.pause_reason ? ` · ${applyQueueState.pause_reason}` : ""}
                  {applyQueueState.rate_limit_reset_at
                    ? ` · reset ${new Date(applyQueueState.rate_limit_reset_at * 1000).toLocaleString()}`
                    : ""}
                </p>
              )}

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
                            : img.generated_alt || "",
                        userEdited: false
                      };
                      const genStatus = imageGenStatus[key];
                      const genErr = imageGenError[key];
                      const itemApplyStatus = applyItemStatus[key] || "idle";
                      const itemApplyError =
                        applyQueueState?.items.find(
                          (x) => x.uri === post.uri && x.image_index === img.index
                        )?.error || "";

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
                                ) : genStatus === "generating" || genStatus === "queued" ? (
                                  <em>(generating...)</em>
                                ) : genStatus === "error" ? (
                                  <em>(generation error: {genErr || "unknown"})</em>
                                ) : (generationConfig || result.alt_generation_enabled) ? (
                                  <em>(no suggestion returned)</em>
                                ) : (
                                  <em>(add an LLM API key above to enable suggestions)</em>
                                )}
                              </span>
                            </div>

                            <div className="meta-row">
                              <span className="meta-label">Generation status</span>
                              <span className="meta-value">
                                {genStatus ? genStatus : <em>not queued</em>}
                                {genStatus === "error" && genErr && <span role="alert"> · {genErr}</span>}
                              </span>
                            </div>

                            <div className="meta-row">
                              <span className="meta-label">Apply queue status</span>
                              <span className={`meta-value apply-status apply-${itemApplyStatus}`}>
                                {getApplyStatusLabel(itemApplyStatus)}
                                {itemApplyError ? ` · ${itemApplyError}` : ""}
                              </span>
                            </div>

                            <div className="meta-row">
                              {generationRunning && <span className="card-help">To regenerate, wait for the batch to finish or use Stop Generation.</span>}
                              <button
                                type="button"
                                className="regen-btn"
                                onClick={() => onRegenerateAlt(post, img)}
                                disabled={resettingDrafts || loading || generationRunning || generationStopping || Object.values(regeneratingKeyMap).some(Boolean)}
                                title={generationRunning ? "Wait for generation to finish, or stop the batch first." : undefined}
                              >
                                {regeneratingKeyMap[key]
                                  ? "Regenerating..."
                                  : "Regenerate alt-text"}
                              </button>
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
                                href={atUriToBskyWebUrl(post.uri, result.handle)}
                                target="_blank"
                                rel="noreferrer"
                                className="post-link"
                              >
                                View post
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
          Changes are persisted locally and only marked applied after verification.
        </span>
      </footer>
    </div>
  );
};

export default App;
