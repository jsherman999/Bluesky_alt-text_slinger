import copy
import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from backend import main, db, alt_text_gen

URI = 'at://did:plc:test/app.bsky.feed.post/one'

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init_db()
    main.APPLY_JOBS.clear()
    main.GEN_JOBS.clear()


def rendered_payload(alt, quoted=False):
    embed = {'$type': 'app.bsky.embed.images#view', 'images': [{'alt': alt}]}
    if quoted:
        embed = {'$type': 'app.bsky.embed.recordWithMedia#view', 'media': embed}
    return {'thread': {'post': {'embed': embed}}}


@pytest.mark.parametrize('quoted', [False, True])
def test_verification_uses_visible_embed_not_stored_record(monkeypatch, quoted):
    payload = rendered_payload('old', quoted)
    urls = []
    def fetch(url, **kwargs):
        urls.append(url)
        # Repository storage can agree while rendered post still disagrees.
        if 'getRecord' in url:
            return io.BytesIO(json.dumps({'value': {'embed': {'$type': 'app.bsky.embed.images', 'images': [{'alt': 'new'}]}}}).encode())
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(main.urllib.request, 'urlopen', fetch)
    assert main.verify_alts_via_public_api(URI, {0: 'new'}, retries=1)
    assert all('getPostThread' in url for url in urls)
    payload.update(rendered_payload('new', quoted))
    assert main.verify_alts_via_public_api(URI, {0: 'new'}, retries=1) is None


def test_confirmed_write_checks_public_once_without_debug_or_rewrite(monkeypatch):
    record = {'$type': 'app.bsky.feed.post', 'text': 'keep me', 'embed': {'$type': 'app.bsky.embed.images', 'images': [{'alt': 'old'}]}}
    repo = Mock()
    repo.get_record.side_effect = lambda **kw: {'value': copy.deepcopy(record), 'cid': 'original-cid'}
    def put(data):
        assert data['swapRecord'] == 'original-cid'
        assert data['record']['text'] == 'keep me'
        record.update(data['record'])
        return {'validationStatus': 'valid'}
    repo.put_record.side_effect = put
    client = SimpleNamespace(com=SimpleNamespace(atproto=SimpleNamespace(repo=repo)))
    verify = Mock(return_value='still old')
    monkeypatch.setattr(main, 'verify_alts_via_public_api', verify)
    debug = Mock(side_effect=AssertionError('redundant diagnostics'))
    monkeypatch.setattr(main, 'debug_compare_alt_views', debug)
    with pytest.raises(main.PropagationPendingError):
        main._apply_updates_for_uri(client, 'test', URI, [main.AltUpdate(uri=URI, image_index=0, new_alt='new')], verify_public=True)
    assert repo.put_record.call_count == 1
    assert verify.call_args.kwargs['retries'] == 1
    debug.assert_not_called()


def seed_job(count):
    updates = [{'uri': URI + str(i), 'image_index': 0, 'new_alt': 'new'} for i in range(count)]
    db.create_apply_job('job', 'test', count)
    db.insert_apply_job_items('job', updates)
    db.set_apply_job_status('job', 'running')
    main.APPLY_JOBS['job'] = {'handle': 'test', 'app_password': 'fake', 'min_interval_s': 0}
    return updates


def test_propagation_checks_rotate_past_stale_first_post():
    rows = seed_job(3)
    for row in rows:
        db.mark_apply_items('job', row['uri'], [{'image_index': 0, 'status': 'propagating'}])
    first = db.claim_next_propagating_uri_group('job')[0]['uri']
    db.mark_apply_items('job', first, [{'image_index': 0, 'status': 'propagating'}])
    assert db.claim_next_propagating_uri_group('job')[0]['uri'] != first


def test_long_queue_finishes_even_when_public_never_updates(monkeypatch):
    rows = seed_job(12)
    monkeypatch.setattr(main, 'Client', Mock())
    now = [1000.0]
    def sleep(seconds):
        now[0] += max(seconds, 1)
    monkeypatch.setattr(main.time, 'time', lambda: now[0])
    monkeypatch.setattr(main.time, 'sleep', sleep)
    apply = Mock(side_effect=main.PropagationPendingError('Saved to PDS'))
    monkeypatch.setattr(main, '_apply_updates_for_uri', apply)
    monkeypatch.setattr(main, 'verify_alts_via_public_api', Mock(return_value='old'))
    main._run_apply_queue_worker('job')
    state = main.apply_queue_state('job')
    assert state.status == 'completed'
    assert state.processed_items == state.failed_items == len(rows)
    assert state.success_items == 0
    assert apply.call_count == len(rows)  # No repeated writes during verification.
    assert 'job' not in main.APPLY_JOBS


def test_login_failure_does_not_leave_dead_runtime(monkeypatch):
    seed_job(1)
    client = Mock()
    client.login.side_effect = RuntimeError('bad credentials')
    monkeypatch.setattr(main, 'Client', Mock(return_value=client))
    main._run_apply_queue_worker('job')
    assert db.get_apply_job('job')['status'] == 'paused'
    assert 'job' not in main.APPLY_JOBS


def test_unexpected_worker_error_requeues_and_can_resume(monkeypatch):
    seed_job(1)
    db.claim_next_pending_uri_group('job')
    monkeypatch.setattr(main, '_process_apply_queue', Mock(side_effect=RuntimeError('interrupted')))
    main._run_apply_queue_worker('job')
    assert db.get_apply_job('job')['status'] == 'paused'
    assert db.get_apply_job_item_statuses('job')[0]['status'] == 'pending'
    assert 'job' not in main.APPLY_JOBS


def test_generation_failure_is_reported_and_next_image_runs(monkeypatch):
    job = {'cancel_event': threading.Event(), 'lock': threading.Lock(), 'events': [], 'next_seq': 1, 'processed_items': 0, 'generated_items': 0, 'total_items': 2, 'done': False}
    main.GEN_JOBS['gen'] = job
    monkeypatch.setattr(main, 'generate_alt_text', Mock(side_effect=[RuntimeError('provider timed out'), 'a cat']))
    req = main.GenerateStartRequest(handle='test', items=[main.GenerateAltItem(uri=URI, image_index=i, fullsize_url='https://example.test/image') for i in range(2)])
    main._run_generation_job('gen', req)
    assert job['done'] and job['processed_items'] == 2 and job['generated_items'] == 1
    results = [e for e in job['events'] if e['type'] == 'item_result']
    assert results[0]['error'] == 'provider timed out'
    assert results[1]['generated_alt'] == 'a cat'


def test_provider_error_is_not_silently_converted_to_empty_suggestion(monkeypatch):
    client = Mock()
    client.chat.completions.create.side_effect = RuntimeError('provider down')
    monkeypatch.setattr(alt_text_gen, '_client', client)
    with pytest.raises(RuntimeError, match='provider down'):
        alt_text_gen.generate_alt_text('https://example.test/image')
