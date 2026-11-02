"""A withdrawn reference must not start or strand study prediction workers."""
import pytest

from mint.comparison.research import ResearchRegistry
from mint.comparison.studies import StudyManager
from tests.test_comparison import fake_backend
from tests.test_platform_lifecycle import manager
from tests.test_reference_withdrawal import _with_reference, _withdraw


def _setup(manager, tmp_path, monkeypatch):
    monkeypatch.setattr('mint.comparison.studies.backend_spec', fake_backend)
    comp, reference = _with_reference(manager, tmp_path)
    registry = ResearchRegistry(manager)
    protocol = registry.protocol(name='Frozen before correction', comparisons=[comp['id']])
    studies = StudyManager(manager, registry, None, autostart=False)
    return studies, protocol, reference


def test_withdrawn_protocol_is_rejected_before_a_study_is_queued(manager, tmp_path, monkeypatch):
    studies, protocol, reference = _setup(manager, tmp_path, monkeypatch)
    try:
        _withdraw(manager, reference)
        with pytest.raises(ValueError, match='withdrawn'):
            studies.create(kind='regression', protocol_id=protocol['id'], selections=[{'algorithm': 'mint'}])
        with manager.db() as db:
            assert db.execute('SELECT count(*) FROM studies').fetchone()[0] == 0
    finally:
        studies.close()


@pytest.mark.parametrize('operation', ['execute', 'resume'])
def test_reference_withdrawn_after_submission_never_creates_predictions(manager, tmp_path, monkeypatch, operation):
    studies, protocol, reference = _setup(manager, tmp_path, monkeypatch)
    try:
        job = studies.create(kind='regression', protocol_id=protocol['id'],
                             selections=[{'algorithm': 'mint', 'options': {'hand_mode': 'hard'}}])
        _withdraw(manager, reference)
        if operation == 'resume':
            studies._set(job['id'], status='interrupted')
        monkeypatch.setattr(manager, 'create', lambda *args, **kwargs: pytest.fail('Invalid GT must not create predictions'))
        with pytest.raises(ValueError, match='withdrawn'):
            (studies._execute if operation == 'execute' else studies.resume)(job['id'])
        assert studies.get(job['id'])['children'] == {}
    finally:
        studies.close()


def test_reference_withdrawal_during_child_admission_cancels_only_owned_predictions(manager, tmp_path, monkeypatch):
    studies, protocol, reference = _setup(manager, tmp_path, monkeypatch)
    try:
        job = studies.create(kind='regression', protocol_id=protocol['id'],
                             selections=[{'algorithm': 'mint', 'options': {'hand_mode': 'hard'}}])
        create = manager.create
        def create_then_withdraw(*args, **kwargs):
            comparison = create(*args, **kwargs)
            _withdraw(manager, reference)
            return comparison
        monkeypatch.setattr(manager, 'create', create_then_withdraw)
        with pytest.raises(ValueError, match='withdrawn'):
            studies._execute(job['id'])
        child = studies.get(job['id'])['children']['0:None:0']
        assert child['owned'] and child['runs'] == child['owned']
        assert all(manager.run(key)['status'] == 'cancelled' for key in child['owned'])
        assert manager.run(protocol['entries'][0]['reference_run'])['annotation_withdrawal']
        original = manager.comparison(protocol['entries'][0]['comparison_id'])
        assert all(run['status'] == 'completed' for run in original['runs'] if run['algorithm'] != 'reference')
    finally:
        studies.close()


def test_completed_study_reports_reference_withdrawal_without_rewriting_history(manager, tmp_path, monkeypatch):
    studies, protocol, reference = _setup(manager, tmp_path, monkeypatch)
    try:
        job = studies.create(kind='regression', protocol_id=protocol['id'], selections=[{'algorithm': 'mint'}])
        studies._execute(job['id'])
        before = studies.get(job['id'])
        assert before['status'] == 'completed' and before['rows'] and before['evaluation_id']
        with manager.db() as db:
            frozen = tuple(db.execute('SELECT payload,state FROM studies WHERE id=?', (job['id'],)).fetchone())
        _withdraw(manager, reference)
        monkeypatch.setattr(manager, 'create', lambda *a, **k: pytest.fail('Reading invalidated history must not admit a GPU task'))
        monkeypatch.setattr(manager, 'resume', lambda *a, **k: pytest.fail('Invalid reference must not resume a GPU task'))
        current = studies.get(job['id'])
        assert current['status'] == 'failed' and current['source_invalidated']
        assert 'withdrawn' in current['error']
        assert current['rows'] == before['rows'] and current['evaluation_id'] == before['evaluation_id']
        assert studies.task_rows()[0]['status'] == 'failed'
        with pytest.raises(ValueError, match='withdrawn'):
            studies.resume(job['id'])
        with manager.db() as db:
            assert tuple(db.execute('SELECT payload,state FROM studies WHERE id=?', (job['id'],)).fetchone()) == frozen
    finally:
        studies.close()
