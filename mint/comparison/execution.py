"""Runtime intent is separate from numerical algorithm options and cache evidence."""
from __future__ import annotations

PRIORITIES = {'background': 0, 'batch': 1, 'interactive': 2}
RUNTIME_KEYS = frozenset({'resource_policy', 'max_gpus'})


def execution_options(value=None):
    value = dict(value or {})
    if set(value) - {'priority', 'cache'}:
        raise ValueError('Unknown execution fields')
    priority, cache = value.get('priority', 'interactive'), value.get('cache', 'exact')
    if priority not in PRIORITIES or cache not in {'exact', 'compatible', 'fresh'}:
        raise ValueError('priority: interactive/batch/background; cache: exact/compatible/fresh')
    return {'priority': priority, 'cache': cache}


def numerical_options(options):
    return {key: value for key, value in options.items() if key not in RUNTIME_KEYS}


def compatible_request(original, candidate):
    """Only complete native runs with identical numerical/source/backend identity."""
    return (not original.get('import') and not candidate.get('import') and not original.get('components')
            and original['sample_id'] == candidate['sample_id']
            and original['algorithm'] == candidate['algorithm']
            and original['backend_version'] == candidate['backend_version']
            and numerical_options(original['options']) == numerical_options(candidate['options']))
