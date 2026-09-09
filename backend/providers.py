"""Request-scoped provider credentials; never persisted or included in responses."""
from typing import Literal
import re
import httpx
from pydantic import BaseModel, SecretStr

class ProviderKey(BaseModel):
    api_key: SecretStr
    provider: Literal['auto', 'openai', 'openrouter'] = 'auto'

class GenerationConfig(ProviderKey):
    model: str

BASE_URLS = {'openai': 'https://api.openai.com/v1', 'openrouter': 'https://openrouter.ai/api/v1'}

def identify(config: ProviderKey) -> str:
    key = config.api_key.get_secret_value().strip()
    if not key:
        raise ValueError('Enter an API key.')
    if config.provider != 'auto':
        return config.provider
    if key.startswith('sk-or-'):
        return 'openrouter'
    if key.startswith(('sk-proj-', 'sk-svcacct-')):
        return 'openai'
    raise ValueError('This key prefix is ambiguous. Select OpenAI or OpenRouter before loading models.')

def discover(config: ProviderKey) -> dict:
    provider = identify(config)
    headers = {'Authorization': 'Bearer ' + config.api_key.get_secret_value().strip()}
    with httpx.Client(timeout=20) as client:
        # The OpenRouter model catalog is public; validate the key separately.
        if provider == 'openrouter':
            response = client.get(BASE_URLS[provider] + '/key', headers=headers)
            response.raise_for_status()
        response = client.get(BASE_URLS[provider] + '/models', headers=headers)
        response.raise_for_status()
        entries = response.json()['data']
    models = []
    for entry in entries:
        model_id = entry['id']
        if provider == 'openrouter':
            architecture = entry.get('architecture', {})
            supported = ('image' in architecture.get('input_modalities', [])
                         and 'text' in architecture.get('output_modalities', []))
        else:
            # OpenAI's list endpoint has no modality metadata. Restrict to known
            # image-understanding families; exclude specialized variants.
            supported = bool(re.match(r'^gpt-(4o|4\.1|5)(?:[.\-]|$)', model_id)) and not any(
                word in model_id for word in ('audio', 'realtime', 'transcribe', 'tts', 'search', 'codex'))
        if supported:
            models.append({'id': model_id, 'name': entry.get('name', model_id)})
    return {'provider': provider, 'models': sorted(models, key=lambda x: x['id']),
            'note': 'Image-description models only. Listing does not guarantee credits or model access.'}

def public_error(exc: Exception) -> str:
    status = getattr(getattr(exc, 'response', None), 'status_code', None) or getattr(exc, 'status_code', None)
    if status in (401, 403):
        return 'The provider rejected this key or its permissions. Check the key and selected provider.'
    if status == 429:
        return 'Provider rate limit or quota reached. Check your credits and try again later.'
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return 'The provider timed out. Try again.'
    return 'The provider request failed. Check model access, credits, and connectivity.'
