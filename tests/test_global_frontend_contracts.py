"""Task navigation reads use exact persisted categories without controlling executors."""
from __future__ import annotations

import contextlib
import copy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from mint.comparison.task_index import TaskIndex


@pytest.fixture
def task_store(tmp_path):
    statements = []
    root = tmp_path / 'read-contracts'
    root.mkdir()
    state = {'running': {'owner': 123, 'status': 'running'}, 'queued': {'status': 'queued'}}

    @contextlib.contextmanager
    def db():
        connection = sqlite3.connect(root / 'jobs.sqlite')
        connection.row_factory = sqlite3.Row
        connection.set_trace_callback(statements.append)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Listing tasks must not call an executor')

    manager = SimpleNamespace(root=root, db=db, run=forbidden, cancel=forbidden, resume=forbidden,
                              executor_state=state, statements=statements)
    return manager


def seed(index):
    rows = []
    for order, (kind, status) in enumerate([
        ('inference', 'running'), ('batch', 'completed'), ('evaluation', 'failed'),
        ('workflow', 'queued'), ('study', 'interrupted'), ('render', 'completed'), ('export', 'partial'),
        ('mint_workspace', 'queued'), ('mint_batch', 'running'), ('mint_benchmark', 'completed'),
        ('code_diff', 'failed'), ('dataset_analysis', 'waiting_gpu'),
    ]):
        rows.append({'id': 'job-' + kind, 'kind': kind, 'status': status,
                     'label': kind + ' source', 'updated': order + 1, 'progress': {}})
    index.sync(rows)
    return rows


@pytest.mark.parametrize('kind', [
    'inference', 'batch', 'evaluation', 'workflow', 'study', 'render', 'export',
    'mint_workspace', 'mint_batch', 'mint_benchmark', 'code_diff', 'dataset_analysis',
])
def test_exact_task_category_does_not_include_other_executors(task_store, kind):
    index = TaskIndex(task_store)
    original = seed(index)
    state = copy.deepcopy(task_store.executor_state)
    page = index.page(kind=kind)
    assert page['total'] == 1
    assert [row['kind'] for row in page['tasks']] == [kind]
    assert task_store.executor_state == state
    assert original == seed_rows_without_observations(original)


def seed_rows_without_observations(rows):
    # sync must not attach observation state to the authoritative incoming rows.
    return [{key: value for key, value in row.items() if key not in {'phase', 'observed_at', 'attempt'}}
            for row in rows]


def test_diagnostic_category_groups_five_legacy_sources_and_combines_status(task_store):
    index = TaskIndex(task_store)
    seed(index)
    expected = {'mint_workspace', 'mint_batch', 'mint_benchmark', 'code_diff', 'dataset_analysis'}
    all_rows = index.page(kind='diagnostic')
    assert all_rows['total'] == 5 and {row['kind'] for row in all_rows['tasks']} == expected
    active = index.page(kind='diagnostic', status='active')
    assert active['total'] == 3
    assert {row['kind'] for row in active['tasks']} == {'mint_workspace', 'mint_batch', 'dataset_analysis'}
    attention = index.page(kind='diagnostic', status='attention')
    assert attention['total'] == 1 and attention['tasks'][0]['kind'] == 'code_diff'
    exact = index.page(kind='diagnostic', status='completed')
    assert exact['total'] == 1 and exact['tasks'][0]['kind'] == 'mint_benchmark'


def test_category_status_literal_search_and_sql_paging_share_one_denominator(task_store):
    index = TaskIndex(task_store)
    rows = [
        {'id': f'train-{number}', 'kind': 'workflow', 'status': 'queued' if number < 5 else 'completed',
         'updated': 100 - number, 'label': 'Needle% source' if number < 4 else 'Needle_other', 'progress': {}}
        for number in range(7)
    ]
    rows.append({'id': 'other', 'kind': 'render', 'status': 'queued', 'updated': 999,
                 'label': 'Needle% source', 'progress': {}})
    index.sync(rows)
    task_store.statements.clear()
    page = index.page(kind='workflow', status='active', query='NEEDLE%', offset=2, limit=2)
    assert page['total'] == 4 and page['offset'] == 2
    assert [row['id'] for row in page['tasks']] == ['train-2', 'train-3']
    assert any('LIMIT 2 OFFSET 2' in sql and 'ORDER BY updated DESC,key' in sql
               for sql in task_store.statements), 'Pagination must happen in the SQL read, before JSON decoding'
    assert index.page(kind='workflow', query='Needle_')['total'] == 3
    assert index.page(kind='workflow', status='completed', query='Needle%')['total'] == 0


@pytest.mark.parametrize('kind', ['unknown', "workflow') OR 1=1 --", '', 'Workflow'])
def test_unknown_task_category_is_rejected_without_changing_index_or_executor(task_store, kind):
    index = TaskIndex(task_store)
    seed(index)
    state = copy.deepcopy(task_store.executor_state)
    with task_store.db() as db:
        before = [tuple(row) for row in db.execute('SELECT * FROM platform_tasks ORDER BY key')]
        events = db.execute('SELECT count(*) FROM platform_events').fetchone()[0]
    with pytest.raises(ValueError, match='known task category'):
        index.page(kind=kind, status='active')
    with task_store.db() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM platform_tasks ORDER BY key')] == before
        assert db.execute('SELECT count(*) FROM platform_events').fetchone()[0] == events
    assert task_store.executor_state == state


def test_workspace_task_read_forwards_the_complete_filter_contract(task_store):
    from mint.comparison.workspace import WorkspaceServices
    index = TaskIndex(task_store)
    seed(index)
    service = object.__new__(WorkspaceServices)
    service.index = index
    service.legacy = None
    refreshes = []
    service.refresh_index = lambda: refreshes.append('observed')
    service.resources = SimpleNamespace(snapshot=lambda: {'source': 'cached resources'})
    service.cached_predictions = lambda: []
    page = service.tasks(kind='diagnostic', status='active', query='batch', offset=0, limit=1)
    assert refreshes == ['observed']
    assert page['total'] == 1 and page['tasks'][0]['kind'] == 'mint_batch'
    assert page['resources'] == {'source': 'cached resources'}
    assert page['cached_predictions'] == []


def test_workspace_api_passes_kind_status_query_and_paging_without_starting_workers(task_store, monkeypatch):
    from flask import Flask
    from mint.comparison.api import comparison_blueprint
    import mint.comparison.collections as collections_module
    import mint.comparison.research as research_module
    import mint.comparison.tools as tools_module
    import mint.comparison.workflows as workflows_module
    import mint.comparison.workspace as workspace_module

    index = TaskIndex(task_store)
    seed(index)
    observed = []

    class FakeWorkspace:
        def __init__(self, *_args):
            self.workflows = None

        def tasks(self, **filters):
            observed.append(filters)
            return index.page(**filters)

    monkeypatch.setattr('mint.comparison.api.ComparisonManager', lambda: task_store)
    monkeypatch.setattr(collections_module, 'CollectionManager', lambda *_args: SimpleNamespace())
    monkeypatch.setattr(tools_module, 'RenderTools', lambda *_args: SimpleNamespace())
    monkeypatch.setattr(research_module, 'ResearchRegistry', lambda *_args: SimpleNamespace())
    monkeypatch.setattr(workflows_module, 'WorkflowManager', lambda *_args: SimpleNamespace())
    monkeypatch.setattr(workspace_module, 'WorkspaceServices', FakeWorkspace)
    app = Flask(__name__)
    app.register_blueprint(comparison_blueprint())
    client = app.test_client()
    response = client.get('/api/comparison/workspace?kind=diagnostic&status=active&query=batch&offset=0&limit=1')
    assert response.status_code == 200 and response.json['total'] == 1
    assert response.json['tasks'][0]['kind'] == 'mint_batch'
    assert observed[-1] == {'kind': 'diagnostic', 'status': 'active', 'query': 'batch', 'offset': '0', 'limit': '1'}
    invalid = client.get('/api/comparison/workspace?kind=not-a-category')
    assert invalid.status_code == 400 and 'known task category' in invalid.json['error']
    assert task_store.executor_state['running']['status'] == 'running'
