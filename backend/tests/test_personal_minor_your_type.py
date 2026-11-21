"""Fresh synthetic HTTP regression for Personal age downgrade -> Your Type erasure."""
import json
from uuid import uuid4

import pytest
from backend import your_type as yt
from backend.tests.test_individual_answer_history import run_case, selection, observations
from backend.tests.test_your_type import manifest


@pytest.mark.parametrize('age_payload', [
    {'demographics_patch': {'age': 17}},
    {'demographics': {'age': 17, 'gender': 'Man'}},
])
def test_personal_minor_conversion_erases_your_type_and_invalidates_consent(monkeypatch, tmp_path, age_payload):
    path = tmp_path / 'catalog.v1.json'
    path.write_text(json.dumps(manifest()), encoding='utf8')
    monkeypatch.setattr(yt, 'MANIFEST', path)
    yt._load_catalog.cache_clear()

    async def scenario(db, call, quiz, server):
        question = quiz['short'][0]
        await call('PUT', '/me/about', {'answers': {question['id']: selection(question)}})
        ordinary = (await observations(db, domain='everyday'))[0]
        await db.users.update_one({'user_id': 'owner'}, {'$set': {
            'your_type_research_version': 'your-type-research-v2',
            'your_type_research_revision': 4, 'your_type_research_at': '2026-11-13T00:00:00+00:00'}})
        start_body = {'expected_member': 'owner', 'request_id': str(uuid4()), 'audience': 'women',
                      'sexuality': 'straight', 'viewer_gender': 'Man', 'adult_confirmed': True}
        own = await call('POST', '/your-type/sessions', start_body)
        peer = await call('POST', '/your-type/sessions', {**start_body, 'expected_member': 'peer', 'request_id': str(uuid4())}, uid='peer')
        face = own['cards'][0]['face_id']
        await call('POST', '/your-type/sessions/' + own['id'] + '/actions', {
            'action_id': str(uuid4()), 'expected_revision': 0, 'kind': 'like', 'face_id': face,
            'viewed_face_ids': [face], 'dwell_ms': {face: 500}})
        sensitive = await observations(db, domain='your_type')
        assert len(sensitive) == 2
        await call('PUT', '/me/about', {'expected_member': 'owner', **age_payload})
        owner = await db.users.find_one({'user_id': 'owner'})
        assert 'your_type_research_version' not in owner
        assert {'revision': owner['your_type_research_revision'],
                'live_sessions': await db.your_type_sessions.count_documents({'user_id': 'owner'}),
                'history_events': len(await observations(db, domain='your_type'))} == {
                    'revision': 5, 'live_sessions': 0, 'history_events': 0}
        assert await db.your_type_sessions.find_one({'_id': own['id']}) == {'_id': own['id'], 'deleted': True}
        assert await db.your_type_sessions.count_documents({'user_id': 'owner'}) == 0
        assert await observations(db, domain='your_type') == []
        for event in sensitive:
            erased = await db.answer_history.find_one({'_id': event['id']})
            assert erased.get('purged') is True and 'after' not in erased and 'owner_id' not in erased
        assert not (await db.answer_history.find_one({'_id': ordinary['id']})).get('purged')
        assert (await db.your_type_sessions.find_one({'_id': peer['id']}))['user_id'] == 'peer'
        exported = await call('GET', '/me/export')
        assert exported['your_type_sessions'] == []
        assert not any(row.get('domain') == 'your_type' for row in exported['answer_history'])
        await call('PATCH', '/your-type/research', {'expected_member': 'owner', 'version': 'your-type-research-v2',
            'enabled': False, 'revision': 4}, status=409)
        await call('GET', '/your-type/sessions/' + own['id'], status=403)

    run_case(monkeypatch, scenario)


def test_personal_adult_demographic_edit_preserves_private_your_type_history(monkeypatch, tmp_path):
    path = tmp_path / 'catalog.v1.json'
    path.write_text(json.dumps(manifest()), encoding='utf8')
    monkeypatch.setattr(yt, 'MANIFEST', path)
    yt._load_catalog.cache_clear()

    async def scenario(db, call, quiz, server):
        await db.users.update_one({'user_id': 'owner'}, {'$set': {
            'your_type_research_version': 'your-type-research-v2', 'your_type_research_revision': 4}})
        own = await call('POST', '/your-type/sessions', {'expected_member': 'owner', 'request_id': str(uuid4()),
            'audience': 'women', 'sexuality': 'straight', 'viewer_gender': 'Man', 'adult_confirmed': True})
        await call('PUT', '/me/about', {'expected_member': 'owner', 'demographics_patch': {'age': 31}})
        owner = await db.users.find_one({'user_id': 'owner'})
        assert owner['your_type_research_version'] == 'your-type-research-v2'
        assert owner['your_type_research_revision'] == 4
        assert (await db.your_type_sessions.find_one({'_id': own['id']}))['user_id'] == 'owner'
        assert len(await observations(db, domain='your_type')) == 1

    run_case(monkeypatch, scenario)

