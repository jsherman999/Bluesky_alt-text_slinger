from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import pytest
from fastapi.testclient import TestClient
from backend import providers, main, alt_text_gen


def test_detection_does_not_guess_ambiguous_keys():
    assert providers.identify(providers.ProviderKey(api_key='sk-or-fake')) == 'openrouter'
    assert providers.identify(providers.ProviderKey(api_key='sk-proj-fake')) == 'openai'
    with pytest.raises(ValueError, match='ambiguous'):
        providers.identify(providers.ProviderKey(api_key='sk-ambiguous'))


def test_catalog_validates_router_key_and_filters_for_image_input(monkeypatch):
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock()
    response = Mock()
    response.json.return_value = {'data': [
        {'id': 'vision', 'architecture': {'input_modalities': ['text','image'], 'output_modalities': ['text']}},
        {'id': 'text', 'architecture': {'input_modalities': ['text'], 'output_modalities': ['text']}},
        {'id': 'image-out', 'architecture': {'input_modalities': ['image'], 'output_modalities': ['image']}},
    ]}
    client.get.return_value = response
    monkeypatch.setattr(providers.httpx, 'Client', Mock(return_value=client))
    result = providers.discover(providers.ProviderKey(api_key='sk-or-fake'))
    assert [m['id'] for m in result['models']] == ['vision']
    assert client.get.call_args_list[0].args[0].endswith('/key')
    assert 'sk-or-fake' not in str(result)


def test_api_errors_never_echo_provider_credentials(monkeypatch):
    key = 'sk-proj-private-example'
    monkeypatch.setattr(providers, 'discover', Mock(side_effect=RuntimeError(key)))
    response = TestClient(main.app).post('/api/providers/models', json={'api_key': key})
    assert response.status_code == 400
    assert key not in response.text


def test_generation_uses_request_key_and_model_without_mutating_defaults(monkeypatch):
    client = Mock()
    client.responses.create.return_value = SimpleNamespace(output_text='A red bicycle.')
    constructor = Mock(return_value=client)
    monkeypatch.setattr(alt_text_gen, 'OpenAI', constructor)
    original = alt_text_gen._client
    config = providers.GenerationConfig(api_key='sk-proj-private', model='gpt-4.1', provider='openai')
    result = alt_text_gen.generate_alt_text('https://example.test/image', config=config)
    assert result == 'A red bicycle.'
    assert constructor.call_args.kwargs['api_key'] == 'sk-proj-private'
    assert client.responses.create.call_args.kwargs['model'] == 'gpt-4.1'
    assert client.responses.create.call_args.kwargs['store'] is False
    assert alt_text_gen._client is original
    client.close.assert_called_once()


def test_generation_start_keeps_key_out_of_retained_job_events(monkeypatch):
    thread = Mock()
    monkeypatch.setattr(main.threading, 'Thread', thread)
    req = main.GenerateStartRequest(handle='test', items=[], generation={
        'api_key':'sk-or-private', 'model':'vision', 'provider':'openrouter'})
    result = main.generate_start(req)
    job = main.GEN_JOBS.pop(result.job_id)
    assert 'sk-or-private' not in str(job)
    job_req = thread.call_args.kwargs['args'][1]
    assert job_req.generation.model == 'vision'
    assert 'sk-or-private' not in repr(job_req)
