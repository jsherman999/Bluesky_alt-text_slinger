from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from backend import main, db

URI = 'at://did:plc:test/app.bsky.feed.post/one'

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'reset.db'))
    db.init_db()
    main.GEN_JOBS.clear()
    db.save_scan('test', [{'uri': URI, 'images': [
        {'index': 0, 'alt': '', 'generated_alt': 'unpublished'},
        {'index': 1, 'alt': '', 'generated_alt': 'stale draft'},
    ]}])


def request(items=2):
    return main.ResetDraftsRequest(handle='@test', app_password='fake', items=[
        {'uri': URI, 'image_index': i, 'fullsize_url': 'https://example.test/image'} for i in range(items)])


def client(monkeypatch):
    mock = Mock()
    mock.login.return_value = SimpleNamespace(did='did:plc:test')
    mock.com.atproto.repo.get_record.return_value = {'value': {'embed': {
        '$type': 'app.bsky.embed.images', 'images': [{'alt': ''}, {'alt': 'Saved on Bluesky'}]}}}
    monkeypatch.setattr(main, 'Client', Mock(return_value=mock))
    return mock


def test_reset_preserves_authoritative_saved_alts_and_clears_cache(monkeypatch):
    mock = client(monkeypatch)
    result = main.reset_generation_drafts(request())
    assert [x['alt'] for x in result['images']] == ['', 'Saved on Bluesky']
    assert db.get_generated_alt_map('test') == {}
    conn = db._get_conn()
    rows = conn.execute('SELECT current_alt FROM images ORDER BY image_index').fetchall()
    conn.close()
    assert [r[0] for r in rows] == ['', 'Saved on Bluesky']
    assert mock.com.atproto.repo.get_record.call_count == 1
    mock.com.atproto.repo.put_record.assert_not_called()


def test_incomplete_remote_verification_keeps_all_drafts(monkeypatch):
    client(monkeypatch)
    before = db.get_generated_alt_map('test')
    with pytest.raises(HTTPException):
        main.reset_generation_drafts(request(3))
    assert db.get_generated_alt_map('test') == before


def test_active_generation_rejects_reset(monkeypatch):
    main.GEN_JOBS['job'] = {'handle': 'test', 'done': False}
    with pytest.raises(HTTPException) as exc:
        main.reset_generation_drafts(request())
    assert exc.value.status_code == 409
    assert db.get_generated_alt_map('test')


def test_paused_apply_queue_rejects_reset():
    db.create_apply_job('job', 'test', 1)
    db.set_apply_job_status('job', 'paused')
    with pytest.raises(HTTPException) as exc:
        main.reset_generation_drafts(request())
    assert exc.value.status_code == 409


def test_other_account_records_cannot_clear_cache(monkeypatch):
    mock = client(monkeypatch)
    mock.login.return_value = SimpleNamespace(did='did:plc:other')
    with pytest.raises(HTTPException):
        main.reset_generation_drafts(request())
    assert db.get_generated_alt_map('test')
    mock.com.atproto.repo.get_record.assert_not_called()
