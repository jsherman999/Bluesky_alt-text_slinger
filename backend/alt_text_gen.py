import os
from typing import Optional

from openai import OpenAI  # pip install openai

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ALTGEN_MODEL = os.getenv("ALTGEN_MODEL", "gpt-4o-mini")

_client: Optional[OpenAI] = None
if OPENAI_API_KEY:
    _client = OpenAI(api_key=OPENAI_API_KEY)


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

    try:
        resp = _client.chat.completions.create(
            model=ALTGEN_MODEL,
            messages=[
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
            ],
            max_tokens=120,
            temperature=0.2,
        )
        text = resp.choices[0].message.content or ""
        return text.strip() or None
    except Exception as e:
        print(f"[alt_text_gen] Error generating alt-text: {e}")
        return None