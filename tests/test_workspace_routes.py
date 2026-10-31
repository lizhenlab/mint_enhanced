"""Canonical workspace entry points preserve diagnostic bookmarks without work."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'eval/model_effect'))
from visualization.viewer.routes import create_app


@pytest.fixture
def client(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError('Page navigation must not load models or start jobs')
    store = SimpleNamespace(root=tmp_path, default_mode='mesh_skel',
                            video_eid=forbidden, start_load=forbidden,
                            ensure_dataset_async=forbidden)
    return create_app(store).test_client()


@pytest.mark.parametrize(('source', 'target'), [
    ('/', '/compare'),
    ('/?hub=research&research_tab=models', '/compare?hub=research&research_tab=models'),
    ('/?id=existing&run=child', '/compare?id=existing&run=child'),
    ('/?tool=benchmark', '/diagnostics?tool=benchmark'),
    ('/?task=existing', '/diagnostics?task=existing'),
    ('/?view=results', '/diagnostics?view=results'),
    ('/?tool=analysis-video&path=%2Fdataset%2Fclip+01&x=1&x=2',
     '/diagnostics?tool=analysis-video&path=%2Fdataset%2Fclip+01&x=1&x=2'),
    ('/diagnostics/?tool=code', '/diagnostics?tool=code'),
    ('/compare/?hub=tasks', '/compare?hub=tasks'),
])
def test_canonical_entry_redirects_preserve_queries(client, source, target):
    response = client.get(source)
    assert response.status_code == 302
    assert response.location == target
    assert response.cache_control.no_store


def test_both_workspaces_and_assets_open_without_running_models(client):
    main = client.get('/', follow_redirects=True)
    diagnostic = client.get('/diagnostics')
    assert main.status_code == diagnostic.status_code == 200
    assert 'MOTION WORKSPACE' in main.text
    assert 'diagnosticConfiguration' in diagnostic.text
    for path in ['/style.css', '/app.js', '/i18n.js', '/comparison-assets/app.js']:
        assert client.get(path).status_code == 200


def test_redirects_keep_reverse_proxy_prefix(client):
    response = client.get('/?tool=benchmark', environ_overrides={'SCRIPT_NAME': '/proxy/8011'})
    assert response.location == '/proxy/8011/diagnostics?tool=benchmark'
    response = client.get('/', environ_overrides={'SCRIPT_NAME': '/proxy/8011'})
    assert response.location == '/proxy/8011/compare'
    response = client.get('/compare/?hub=tasks', environ_overrides={'SCRIPT_NAME': '/proxy/8011'})
    assert response.location == '/proxy/8011/compare?hub=tasks'
