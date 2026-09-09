export interface ScanRequest {
  handle: string;
  app_password: string;
  generate_alt?: boolean; // defaults to true on backend
}

export interface ImageInfo {
  index: number;
  thumb_url: string;
  fullsize_url: string;
  alt?: string | null;
  generated_alt?: string | null;
  apply_status?: string | null;
}

export interface PostInfo {
  uri: string;
  cid: string;
  text: string;
  created_at?: string | null;
  images: ImageInfo[];
}

export interface ScanResponse {
  handle: string;
  total_posts: number;
  total_images: number;
  posts: PostInfo[];
  alt_generation_enabled: boolean;
}

export interface ScanProgressEvent {
  type: "progress" | "result" | "error";
  message?: string;
  posts_scanned?: number;
  images_found?: number;
  event?: "post_scanned";
  post_uri?: string;
  post_state?:
    | "no_images"
    | "images_alt_ready"
    | "images_missing_alt"
    | "images_generated_not_applied";
  missing_images_needing_generation?: number;
  data?: ScanResponse;
  error?: {
    status_code?: number;
    detail?: string;
  };
}

export interface GenerateAltItem {
  uri: string;
  image_index: number;
  fullsize_url: string;
  post_text: string;
  current_alt?: string | null;
}

export interface GenerateOneResponse {
  generated_alt?: string | null;
  error?: string | null;
}

export interface GenerateStartResponse {
  job_id: string;
  total_items: number;
}

export interface GenerateJobEvent {
  seq: number;
  type: "started" | "item_started" | "item_result" | "stop_requested" | "complete";
  uri?: string;
  image_index?: number;
  generated_alt?: string | null;
  error?: string | null;
  message?: string;
  total_items?: number;
  processed_items?: number;
  generated_items?: number;
  stop_requested?: boolean;
}

export interface GenerateEventsResponse {
  events: GenerateJobEvent[];
  done: boolean;
  stop_requested: boolean;
  total_items: number;
  processed_items: number;
  generated_items: number;
}

const API_BASE =
  import.meta.env.VITE_API_BASE ??
  `${window.location.protocol}//${window.location.hostname}:8000`;

export async function scanImages(req: ScanRequest): Promise<ScanResponse> {
  const res = await fetch(`${API_BASE}/api/scan`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      ...req,
      generate_alt: req.generate_alt ?? true
    })
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP error ${res.status}`);
  }

  return res.json();
}

export async function scanImagesWithProgress(
  req: ScanRequest,
  onEvent: (event: ScanProgressEvent) => void
): Promise<ScanResponse> {
  const res = await fetch(`${API_BASE}/api/scan/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      ...req,
      generate_alt: req.generate_alt ?? true
    })
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP error ${res.status}`);
  }

  if (!res.body) {
    throw new Error("Scan stream was unavailable.");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult: ScanResponse | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      let event: ScanProgressEvent;
      try {
        event = JSON.parse(trimmed) as ScanProgressEvent;
      } catch {
        continue;
      }
      onEvent(event);
      if (event.type === "result" && event.data) {
        finalResult = event.data;
      } else if (event.type === "error") {
        const detail = event.error?.detail || "Unknown scan error.";
        throw new Error(detail);
      }
    }
  }

  if (!finalResult) {
    throw new Error("Scan finished without returning results.");
  }

  return finalResult;
}

// ---------- Phase 3 apply ----------

export interface AltUpdate {
  uri: string;
  image_index: number;
  new_alt: string;
}

export interface ApplyResultItem {
  uri: string;
  image_index: number;
  success: boolean;
  error?: string;
}

export interface ApplyResponse {
  updated: ApplyResultItem[];
}

export interface ApplyQueueStartResponse {
  job_id: string;
  total_items: number;
}

export interface ApplyQueueItemState {
  uri: string;
  image_index: number;
  status: "pending" | "running" | "propagating" | "applied" | "failed";
  error?: string | null;
  attempts: number;
  updated_at: string;
}

export interface ApplyQueueStateResponse {
  job_id: string;
  handle: string;
  status: "running" | "paused" | "completed";
  total_items: number;
  processed_items: number;
  success_items: number;
  failed_items: number;
  propagating_items: number;
  pending_items: number;
  running_items: number;
  rate_limit_reset_at?: number | null;
  pause_reason?: string | null;
  active_uri?: string | null;
  active_image_indices: number[];
  items: ApplyQueueItemState[];
}

export async function applyAltUpdates(
  handle: string,
  app_password: string,
  updates: AltUpdate[]
): Promise<ApplyResponse> {
  const res = await fetch(`${API_BASE}/api/apply`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      handle,
      app_password,
      updates
    })
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP error ${res.status}`);
  }

  return res.json();
}

export async function startApplyQueue(
  handle: string,
  app_password: string,
  updates: AltUpdate[]
): Promise<ApplyQueueStartResponse> {
  const res = await fetch(`${API_BASE}/api/apply/queue/start`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ handle, app_password, updates })
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP error ${res.status}`);
  }
  return res.json();
}

export async function getApplyQueueState(jobId: string): Promise<ApplyQueueStateResponse> {
  const res = await fetch(`${API_BASE}/api/apply/queue/state/${encodeURIComponent(jobId)}`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP error ${res.status}`);
  }
  return res.json();
}

export async function pauseApplyQueue(jobId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/apply/queue/pause/${encodeURIComponent(jobId)}`, {
    method: "POST"
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP error ${res.status}`);
  }
}

export async function resumeApplyQueue(
  jobId: string,
  handle?: string,
  appPassword?: string
): Promise<void> {
  const params = new URLSearchParams();
  if (handle) params.set("handle", handle);
  if (appPassword) params.set("app_password", appPassword);
  const qs = params.toString();
  const res = await fetch(
    `${API_BASE}/api/apply/queue/resume/${encodeURIComponent(jobId)}${qs ? `?${qs}` : ""}`,
    { method: "POST" }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP error ${res.status}`);
  }
}

export async function startAltGeneration(
  handle: string,
  items: GenerateAltItem[],
  generation?: GenerationConfig
): Promise<GenerateStartResponse> {
  const res = await fetch(`${API_BASE}/api/generate/start`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      handle,
      items,
      generation
    })
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP error ${res.status}`);
  }
  return res.json();
}

export async function pollAltGenerationEvents(
  jobId: string,
  after: number
): Promise<GenerateEventsResponse> {
  const res = await fetch(
    `${API_BASE}/api/generate/events/${encodeURIComponent(jobId)}?after=${after}`
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP error ${res.status}`);
  }
  return res.json();
}

export async function stopAltGeneration(jobId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/generate/stop/${encodeURIComponent(jobId)}`, {
    method: "POST"
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP error ${res.status}`);
  }
}

export async function regenerateAltText(
  handle: string,
  item: GenerateAltItem,
  generation?: GenerationConfig
): Promise<GenerateOneResponse> {
  const res = await fetch(`${API_BASE}/api/generate/one`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      handle,
      item,
      generation
    })
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP error ${res.status}`);
  }
  return res.json();
}

export interface GenerationConfig {
  api_key: string;
  provider: "openai" | "openrouter";
  model: string;
}
export interface ProviderModels {
  provider: "openai" | "openrouter";
  models: {id: string; name: string}[];
  note: string;
}
export async function discoverModels(api_key: string, provider: string): Promise<ProviderModels> {
  const res = await fetch(`${API_BASE}/api/providers/models`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({api_key, provider})
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : "Unable to load models. Try again.");
  }
  return res.json();
}
