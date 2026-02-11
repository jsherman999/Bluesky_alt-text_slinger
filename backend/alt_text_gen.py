import os
from typing import Optional

from openai import OpenAI  # pip install openai

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ALTGEN_MODEL = os.getenv("ALTGEN_MODEL", "")
ALTGEN_MAX_TOKENS = int(os.getenv("ALTGEN_MAX_TOKENS", "80"))
ALTGEN_BASE_URL = os.getenv("ALTGEN_BASE_URL", "")
ALTGEN_HTTP_REFERER = os.getenv("ALTGEN_HTTP_REFERER", "")
ALTGEN_X_TITLE = os.getenv("ALTGEN_X_TITLE", "Bluesky Alt-Text Slinger")

_client: Optional[OpenAI] = None
_default_model = "gpt-4o-mini"
if OPENAI_API_KEY:
    _client = OpenAI(api_key=OPENAI_API_KEY)
elif OPENROUTER_API_KEY:
    # OpenRouter exposes an OpenAI-compatible API.
    base_url = ALTGEN_BASE_URL or "https://openrouter.ai/api/v1"
    headers = {}
    if ALTGEN_HTTP_REFERER:
        headers["HTTP-Referer"] = ALTGEN_HTTP_REFERER
    if ALTGEN_X_TITLE:
        headers["X-Title"] = ALTGEN_X_TITLE
    _client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=base_url,
        default_headers=headers or None,
    )
    _default_model = "openrouter/free"

if not ALTGEN_MODEL:
    ALTGEN_MODEL = _default_model


def is_enabled() -> bool:
    """
    Returns True if alt-text generation is configured (API key present).
    """
    return _client is not None


def generate_alt_text(image_url: str, post_text: Optional[str] = None) -> Optional[str]:
    """
    Generate concise alt-text for the given image URL using an OpenAI
    vision-capable chat model (e.g. gpt-4o / gpt-4o-mini).

    Returns a 1–2 sentence description, or None on error.
    """
    if not _client:
        return None

    context_snippet = (post_text or "").strip()
    if len(context_snippet) > 220:
        context_snippet = context_snippet[:220] + "…"

    user_prompt = (
        "Write concise, objective alt-text for this image for a blind screen-reader user. "
        "Maximum 2 sentences. Do not start with phrases like 'Image of' or 'Photo of'; "
        "just describe the key visual content and any text in the image. "
        f"Here is optional context from the post: {context_snippet or '(no extra context)'}"
    )

    model_lc = ALTGEN_MODEL.lower()
    use_system_message = "gemma" not in model_lc
    if use_system_message:
        messages = [
            {
                "role": "system",
                "content": (
                    "You generate high-quality accessibility alt-text for images. "
                    "Be concrete and neutral, avoid guessing unknown details."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url},
                    },
                ],
            },
        ]
    else:
        # Some providers for Gemma-family models reject system messages.
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url},
                    },
                ],
            }
        ]

    try:
        resp = _client.chat.completions.create(
            model=ALTGEN_MODEL,
            messages=messages,
            max_tokens=ALTGEN_MAX_TOKENS,
            temperature=0.2,
        )
        text = resp.choices[0].message.content or ""
        return text.strip() or None
    except Exception as e:
        print(f"[alt_text_gen] Error generating alt-text: {e}")
        return None
