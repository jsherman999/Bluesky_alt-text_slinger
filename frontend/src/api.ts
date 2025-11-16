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

const API_BASE = "http://localhost:8000";

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

// ---------- Phase 3 apply ----------

export interface AltUpdate {
  uri: string;
  image_index: number;
  new_alt: string;
}

export interface ApplyResultItem {
  uri: string;
  success: boolean;
  error?: string;
}

export interface ApplyResponse {
  updated: ApplyResultItem[];
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