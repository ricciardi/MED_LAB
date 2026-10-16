from copy import deepcopy
import json
from unittest.mock import Mock

import pytest
import requests

from medlab.media_review import CHECKS, PRIVACY_CHECKS, MediaReviewer, MediaReviewError
from medlab.source_media import SourceMediaError
from test_source_media import source_fixture, read


def fixture(tmp_path):
    store, provenance, raw = source_fixture(tmp_path)
    catalog = store.catalog_source('PMC123')
    aid = catalog['figures'][0]['assetIds'][0]
    case = {'caseId': 'mcr-pmc123', 'provenance': provenance,
            'patientProfile': {'age': 42}, 'physicalExams': {}, 'auxiliaryLabs': {'Radiografia': 'Opacidade focal.'}}
    selection = {'assetId': aid, 'examName': 'Radiografia', 'group': 'auxiliary',
        'captionPt': 'Radiografia de tórax.', 'patientRef': 'Paciente único do relato', 'evidenceQuote': 'Verified article.'}
    # Make a sufficiently long, exactly matching source span.
    text = 'The patient underwent chest radiography at initial presentation.'
    path = store.cache_dir / 'PMC123.verified.json'
    value = json.loads(path.read_text()); value['articleText'] = text; path.write_text(json.dumps(value))
    selection['evidenceQuote'] = text
    checks = {key: True for key in CHECKS}
    return store, case, selection, checks, raw


def responder(value, status=200):
    return Mock(status_code=status, json=Mock(return_value={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}],
        'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'prompt_cache_hit_tokens': 50, 'prompt_cache_miss_tokens': 50}}))


def test_machine_review_has_original_pixels_evidence_without_excerpt_and_no_case_mutation(tmp_path):
    store, case, selection, checks, raw = fixture(tmp_path)
    initial = deepcopy(case)
    session = Mock(); session.post.side_effect = [responder({'selections': [selection]}), responder({'checks': checks}), responder({'checks': {k: True for k in PRIVACY_CHECKS}})]
    reviewer = MediaReviewer(store, 'test-key', session=session)
    report = reviewer.run([case])
    result = report['cases'][0]
    assert result['examMedia'][0]['assetId'] == selection['assetId']
    asset = store.lookup(selection['assetId'])
    assert asset['privacyStatus'] == asset['caseBindings'][0]['status'] == 'machine_reviewed'
    assert asset['privacyReview']['reviewMode'] == 'machine'
    assert read(store, asset, caption_pt=result['examMedia'][0]['captionPt'])['data'] == raw
    assert result['examMedia'][0]['captionPt'] == 'Imagem original do exame solicitado: Radiografia.'
    assert case == initial
    saved = (store.media_dir / 'reviews/PMC123.json').read_text()
    assert selection['evidenceQuote'] not in saved
    assert result['selections'][0]['evidence']['start'] == 0
    content = session.post.call_args_list[1].kwargs['json']['messages'][1]['content']
    assert content[1]['image_url']['url'].startswith('data:image/png;base64,')
    assert content[1]['image_url']['detail'] == 'original'
    assert report['usage']['requests'] == 3 and report['usage']['complete']
    assert report['usage']['estimatedUsdRange'][1] > 0
    reviewer.run([case]); assert session.post.call_count == 3  # Cache reuse.


@pytest.mark.parametrize('failed_check', CHECKS)
def test_any_uncertain_visual_check_keeps_image_pending(tmp_path, failed_check):
    store, case, selection, checks, _ = fixture(tmp_path)
    checks[failed_check] = False
    session = Mock(); session.post.side_effect = [responder({'selections': [selection]}), responder({'checks': checks}), responder({'checks': {k: True for k in PRIVACY_CHECKS}})]
    report = MediaReviewer(store, 'test-key', session=session).run([case])
    assert report['cases'][0]['examMedia'] == []
    assert store.lookup(selection['assetId'])['privacyStatus'] == 'pending_visual_review'
    assert not store.lookup(selection['assetId'])['caseBindings']


def test_wrong_exam_and_invented_patient_quote_never_call_vision(tmp_path):
    store, case, selection, checks, _ = fixture(tmp_path)
    selection['examName'] = 'Exame inexistente'
    session = Mock(); session.post.return_value = responder({'selections': [selection]})
    report = MediaReviewer(store, 'test-key', session=session).run([case])
    assert report['cases'][0]['status'] == 'error'
    assert session.post.call_count == 1
    selection['examName'] = 'Radiografia'; selection['evidenceQuote'] = 'This evidence was invented and is not present.'
    session.post.return_value = responder({'selections': [selection]})
    report = MediaReviewer(store, 'test-key', session=session).run([case], refresh=True)
    assert report['cases'][0]['pending'][0]['reason'] == 'missing_exact_patient_evidence'
    assert session.post.call_count == 2


@pytest.mark.parametrize('status', [401, 402, 403, 429, 500])
def test_http_failure_stops_new_calls_without_leaking_body(tmp_path, status):
    store, case, selection, checks, _ = fixture(tmp_path)
    session = Mock(); session.post.return_value = responder({'secret': 'provider-body'}, status)
    reviewer = MediaReviewer(store, 'test-key', session=session)
    report = reviewer.run([case])
    assert report['interrupted'] and session.post.call_count == 1
    assert 'provider-body' not in json.dumps(report)
    assert report['usage']['estimatedUsdRange'] is None


def test_demo_or_missing_key_never_calls_provider(tmp_path):
    store, case, selection, checks, _ = fixture(tmp_path)
    session = Mock()
    for key, demo in [('test', True), ('', False)]:
        result = MediaReviewer(store, key, demo_mode=demo, session=session).run([case])
        assert result['cases'][0]['status'] == 'error'
    session.post.assert_not_called()


def test_machine_privacy_cannot_be_asserted_without_all_checks(tmp_path):
    store, case, selection, checks, _ = fixture(tmp_path)
    with pytest.raises(SourceMediaError):
        store.review_privacy(selection['assetId'], approved=True, reviewer='model', note='Auto', review_mode='machine')


@pytest.mark.parametrize('failed_check', ['noPersonalText', 'noDatesOrRecordNumbers', 'allTextInspectable', 'noUnexplainedText'])
def test_second_independent_privacy_review_can_veto_first_approval(tmp_path, failed_check):
    store, case, selection, checks, _ = fixture(tmp_path)
    privacy = {k: True for k in PRIVACY_CHECKS}; privacy[failed_check] = False
    session = Mock(); session.post.side_effect = [responder({'selections': [selection]}), responder({'checks': checks}), responder({'checks': privacy})]
    report = MediaReviewer(store, 'test-key', session=session).run([case])
    assert report['cases'][0]['examMedia'] == []
    assert store.lookup(selection['assetId'])['privacyStatus'] == 'pending_visual_review'
    final_content = session.post.call_args.kwargs['json']['messages'][1]['content'][0]['text']
    assert 'Verified article' not in final_content
    assert case['auxiliaryLabs']['Radiografia'] not in final_content


def test_existing_assisted_binding_is_not_replaced_by_machine(tmp_path):
    from test_source_media import binding
    store, case, selection, checks, _ = fixture(tmp_path)
    asset = store.download_asset(selection['assetId'])
    store.review_privacy(asset['assetId'], approved=True, reviewer='Assistido', note='Inspeção original')
    binding(store, asset)
    original = store.lookup(asset['assetId'])
    session = Mock()
    report = MediaReviewer(store, 'test-key', session=session).run([case])
    assert report['cases'][0]['examMedia'] == []
    session.post.assert_not_called()
    assert store.lookup(asset['assetId']) == original


def test_pool_is_bounded_and_persistence_runs_on_coordinator_thread(tmp_path):
    import threading
    import time
    store, _, _, _, _ = fixture(tmp_path)
    reviewer = MediaReviewer(store, 'test-key')
    lock = threading.Lock(); active = 0; maximum = 0; writers = []
    def work(case, **kwargs):
        nonlocal active, maximum
        with lock:
            active += 1; maximum = max(maximum, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return {'caseId': case['caseId'], 'status': 'completed', 'examMedia': []}
    reviewer.review_case = work
    reviewer.persist = lambda report: writers.append(threading.get_ident())
    report = reviewer.run([{'caseId': f'mcr-pmc{i}'} for i in range(20)], workers=4)
    assert len(report['cases']) == 20 and maximum == 4
    assert set(writers) == {threading.get_ident()}


@pytest.mark.parametrize('mutation,code', [
    ('duplicate', 'duplicate_asset'), ('asset', 'asset_not_allowed'),
    ('group', 'invalid_group'), ('exam', 'exam_not_allowed'), ('item', 'invalid_item'),
])
def test_selection_diagnostic_is_private_structured_and_reused_without_http(tmp_path, mutation, code):
    store, case, selection, _, _ = fixture(tmp_path)
    invalid = deepcopy(selection)
    if mutation == 'asset': invalid['assetId'] = 'pmcmedia-' + '0' * 32
    elif mutation == 'group': invalid['group'] = 'unexpected group'
    elif mutation == 'exam': invalid['examName'] = 'Unexpected exam'
    elif mutation == 'item': invalid = 'not a selection'
    session = Mock(); session.post.return_value = responder({'selections': [selection, invalid]})
    reviewer = MediaReviewer(store, 'test-key', session=session)
    result = reviewer.run([case])['cases'][0]
    assert any(issue['code'] == code and issue['index'] == 1 for issue in result['diagnostic']['issues'])
    assert result['status'] == 'error' and result['examMedia'] == []
    assert session.post.call_count == 1  # Validate the whole selection before vision/downloads.
    assert store.lookup(selection['assetId'])['downloadStatus'] == 'pending'
    path = store.media_dir / result['selectionCache']
    assert path.stat().st_mode & 0o777 == 0o600
    assert selection['evidenceQuote'] not in path.read_text()
    assert 'test-key' not in path.read_text()
    saved = json.loads((store.media_dir / 'reviews/PMC123.json').read_text())
    assert saved['diagnostic'] == result['diagnostic']
    again = reviewer.run([case])['cases'][0]
    assert again['diagnostic'] == result['diagnostic']
    assert session.post.call_count == 1


def test_selection_cache_resumes_after_vision_failure_and_invalidates_changed_input(tmp_path):
    store, case, selection, checks, _ = fixture(tmp_path)
    first = Mock(); first.post.side_effect = [responder({'selections': [selection]}), responder({}, status=500)]
    initial = MediaReviewer(store, 'test-key', session=first).run([case])
    assert initial['interrupted'] and first.post.call_count == 2
    second = Mock(); second.post.side_effect = [responder({'checks': checks}), responder({'checks': {k: True for k in PRIVACY_CHECKS}})]
    resumed = MediaReviewer(store, 'test-key', session=second).run([case])
    assert second.post.call_count == 2 and resumed['cases'][0]['selectionReused']
    assert len(resumed['cases'][0]['examMedia']) == 1
    case['patientProfile']['age'] = 43
    changed = Mock(); changed.post.return_value = responder({'selections': []})
    updated = MediaReviewer(store, 'test-key', session=changed).run([case])
    assert changed.post.call_count == 1 and not updated['cases'][0]['selectionReused']


def test_corrupt_selection_cache_fails_closed_without_provider(tmp_path):
    store, case, selection, _, _ = fixture(tmp_path)
    selection['examName'] = 'Unknown exam'
    session = Mock(); session.post.return_value = responder({'selections': [selection]})
    first = MediaReviewer(store, 'test-key', session=session).run([case])['cases'][0]
    path = store.media_dir / first['selectionCache']
    value = json.loads(path.read_text()); value['response']['selections'] = []
    path.write_text(json.dumps(value))
    second = Mock()
    result = MediaReviewer(store, 'test-key', session=second).run([case])['cases'][0]
    assert result['status'] == 'error' and 'integridade' in result['error']
    second.post.assert_not_called()


def test_cached_selection_is_portable_only_in_private_media_archive(tmp_path):
    from medlab.media_bundle import export_media, restore_media
    store, case, selection, _, _ = fixture(tmp_path)
    session = Mock(); session.post.return_value = responder({'selections': [selection, selection]})
    result = MediaReviewer(store, 'test-key', session=session).run([case])['cases'][0]
    exported = export_media(store.media_dir, tmp_path / 'media.tar.gz')
    target = tmp_path / 'restored-media'
    restore_media(exported['path'], target, expected_sha256=exported['sha256'])
    cache = target / result['selectionCache']
    assert cache.read_bytes() == (store.media_dir / result['selectionCache']).read_bytes()
    assert cache.stat().st_mode & 0o777 == 0o600
