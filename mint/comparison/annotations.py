"""Immutable 2D corrections with optimistic revision checks and source provenance."""
from __future__ import annotations

import json
import time

import numpy as np

from mint.runtime import digest, file_identity
from .calibration import project_points
from .contracts import json_values, load_result


class AnnotationService:
    def __init__(self, manager, registry):
        self.manager, self.registry = manager, registry
        with manager.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS annotation_heads (sample TEXT, frame INTEGER, revision TEXT, PRIMARY KEY(sample,frame))')

    def context(self, run_id, frame):
        if isinstance(frame, bool) or int(frame) != frame or frame < 0:
            raise ValueError('Frame is a zero-based integer sample position')
        run = self.manager.run(run_id)
        if not run['result']:
            raise ValueError('Select a published prediction')
        path = self.manager.root / 'runs' / run_id / 'result'
        metadata, arrays = load_result(path)
        sample = json.loads((self.manager.root / 'inputs' / run['sample_id'] / 'sample.json').read_text())
        source = sample['request']['source']
        if file_identity(source['path']) != source:
            raise ValueError('Annotation source video changed')
        if frame >= sample['frames']:
            raise ValueError('Frame escapes the input timeline')
        source_frame = int(sample['source_frame_indices'][frame])
        where = np.flatnonzero(arrays['source_frame_indices'] == source_frame)
        if not len(where):
            xy, front = np.full((2, 21, 2), np.nan), np.zeros((2, 21), bool)
        else:
            index = int(where[0]); xy, front = project_points(arrays['joints_camera_m'][index], arrays['K'][index])
            front &= arrays['geometry_valid'][index, :, None]
            front &= (xy[..., 0] >= 0) & (xy[..., 0] < sample['width']) & (xy[..., 1] >= 0) & (xy[..., 1] < sample['height'])
        with self.manager.db() as db:
            row = db.execute('SELECT revision FROM annotation_heads WHERE sample=? AND frame=?', (run['sample_id'], frame)).fetchone()
        return json_values({'run_id': run_id, 'sample_id': run['sample_id'], 'frame': int(frame), 'source_frame': source_frame,
            'width': sample['width'], 'height': sample['height'], 'points': xy, 'visible': front,
            'head': row['revision'] if row else None, 'result_identity': file_identity(path / 'result.json'),
            'source_identity': sample['request']['source'], 'role': '2D human correction; no inferred 3D or MANO GT'})

    def history(self, run_id, frame, *, limit=50):
        context = self.context(run_id, frame)
        rows, seen, key = [], set(), context['head']
        while key and len(rows) < min(200, max(1, int(limit))):
            if key in seen:
                raise ValueError('Annotation revision cycle')
            seen.add(key); row = self.registry.get(key, 'annotation')
            if row['sample_id'] != context['sample_id'] or row['frame'] != context['frame']:
                raise ValueError('Annotation revision timeline mismatch')
            rows.append(row); key = row['parent']
        return {'sample_id': context['sample_id'], 'frame': context['frame'], 'head': context['head'],
                'revisions': rows, 'has_more': bool(key)}

    def save(self, *, run_id, frame, points, visible, reviewer, parent=None, hand_assignment=None, comment='', purpose='review'):
        context = self.context(run_id, frame)
        if not str(reviewer).strip():
            raise ValueError('A reviewer name is required')
        if purpose not in {'review', 'interface_test'}:
            raise ValueError('Declare review or interface_test purpose')
        assignment = hand_assignment or ['left', 'right']
        if sorted(assignment) != ['left', 'right'] or len(assignment) != 2:
            raise ValueError('Map the two source slots to distinct left/right hands')
        points = np.asarray(points, float); visibility = np.asarray(visible)
        if points.shape != (2, 21, 2) or visibility.shape != (2, 21) or visibility.dtype.kind != 'b':
            raise ValueError('Use [2,21,2] pixel points and [2,21] boolean visibility')
        if not np.isfinite(points).all():
            raise ValueError('Coordinates must be finite, including placeholders for invisible points')
        bounds = (points[..., 0] >= 0) & (points[..., 0] < context['width']) & (points[..., 1] >= 0) & (points[..., 1] < context['height'])
        if (visibility & ~bounds).any():
            raise ValueError('Visible points must be inside the source image grid')
        payload = {'name': f'{run_id[:12]} frame {frame}', **{k: context[k] for k in ('run_id', 'sample_id', 'frame', 'source_frame',
            'width', 'height', 'result_identity', 'source_identity')}, 'parent': parent,
            'points': points.tolist(), 'visible': visibility.tolist(), 'hand_assignment': assignment,
            'reviewer': str(reviewer).strip()[:200], 'comment': str(comment)[:4000], 'reviewed_at': time.time(),
            'label_role': 'interface_test_2d' if purpose == 'interface_test' else 'human_corrected_2d_only',
            'purpose': purpose, 'training_eligible': False,
            'supervision': ['pixel_coordinates', 'declared_visibility', 'hand_side'],
            'original_projection': {'points': context['points'], 'visible': context['visible']}}
        key = digest({'kind': 'annotation', 'payload': payload})
        record = {'id': key, 'kind': 'annotation', 'created': time.time(), **payload}
        with self.manager.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM annotation_heads WHERE sample=? AND frame=?', (context['sample_id'], frame)).fetchone()
            if (row['revision'] if row else None) != parent:
                raise ValueError('Annotation changed since it was opened; reload before saving')
            db.execute('INSERT INTO research_records VALUES(?,?,?,?,?)', (key, 'annotation', record['created'],
                json.dumps(record, ensure_ascii=False).lower(), json.dumps(record, ensure_ascii=False)))
            db.execute('INSERT OR REPLACE INTO annotation_heads VALUES(?,?,?)', (context['sample_id'], frame, key))
        return record
