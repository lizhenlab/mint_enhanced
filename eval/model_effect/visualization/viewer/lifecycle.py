"""Generation-pinned requests and asynchronous retirement of model resources."""
from __future__ import annotations

import copy
import functools
import threading
from contextlib import contextmanager

from mint.inference.base import InferenceCancelled


def model_request(method):
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        if getattr(self, '_snapshot_parent', None) is not None:
            self._check_model_generation()
            return method(self, *args, **kwargs)
        eid = args[0] if args and isinstance(args[0], int) else kwargs.get('eid')
        if eid is not None:
            self._validate_input_version(eid)
        with self._model_snapshot(eid) as snapshot:
            result = method(snapshot, *args, **kwargs)
            snapshot._check_model_generation()
            if eid is not None:
                snapshot._validate_input_version(eid)
            return result
    return wrapped


def input_request(method):
    """Keep GT/raw inputs pinned independently of the selected predictor generation."""
    @functools.wraps(method)
    def wrapped(self, eid, *args, **kwargs):
        if getattr(self, '_snapshot_parent', None) is not None:
            return method(self, eid, *args, **kwargs)
        self._validate_input_version(eid)
        with self._load_cv:
            self._active_inputs[eid] = self._active_inputs.get(eid, 0) + 1
        try:
            result = method(self, eid, *args, **kwargs)
            self._validate_input_version(eid)
            return result
        finally:
            with self._load_cv:
                self._active_inputs[eid] -= 1
                self._evict_idle_inputs()
    return wrapped


def serialized_start(method):
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._batch_start_lock:
            return method(self, *args, **kwargs)
    return wrapped


class ModelLifecycle:
    def _init_lifecycle(self):
        self._active_generations = {}
        self._retirements = []
        self._snapshot_parent = None
        self._retired_ids = set()
        self._active_inputs = {}
        self._input_last_used = {}
        self._service_closed = False

    def _generation_current(self):
        parent = getattr(self, '_snapshot_parent', None)
        return parent is None or self._snapshot_generation == parent._load_gen

    def _check_model_generation(self):
        if not self._generation_current():
            raise InferenceCancelled('Model changed; the previous request was cancelled. Retry with the selected model.')

    @contextmanager
    def _model_snapshot(self, eid=None):
        with self._load_cv:
            generation = self._load_gen
            self._active_generations[generation] = self._active_generations.get(generation, 0) + 1
            if eid is not None:
                self._active_inputs[eid] = self._active_inputs.get(eid, 0) + 1
            snapshot = copy.copy(self)
            snapshot._snapshot_parent = self
            snapshot._snapshot_generation = generation
            # Cache dictionaries are replaced on model switch. Old requests retain only old dictionaries.
            snapshot._predictor_ready = threading.Event()
            if self._predictor_ready.is_set():
                snapshot._predictor_ready.set()
        try:
            yield snapshot
        finally:
            with self._load_cv:
                self._active_generations[generation] -= 1
                if eid is not None:
                    import time
                    self._active_inputs[eid] -= 1
                    self._input_last_used[eid] = time.monotonic()
                    self._evict_idle_inputs()
                self._load_cv.notify_all()

    def _evict_idle_inputs(self):
        import os
        maximum = max(1, int(os.environ.get('MINT_VIEWER_CACHED_INPUTS', '4')))
        inactive = sorted((eid for eid in self._raw if not self._active_inputs.get(eid) and eid not in self._batch_active_eids),
                          key=lambda eid: self._input_last_used.get(eid, 0))
        for eid in inactive[:max(0, len(self._raw) - maximum)]:
            self._release_item_cache(eid)

    def _retire_predictor_locked(self, predictor, generation):
        if predictor is None or id(predictor) in self._retired_ids:
            return
        identity = id(predictor)
        self._retired_ids.add(identity)
        finished = threading.Event()
        self._retirements.append(finished)
        def retire():
            try:
                with self._load_cv:
                    self._load_cv.wait_for(lambda: self._active_generations.get(generation, 0) == 0)
                if hasattr(predictor, 'close'):
                    predictor.close()
            except Exception as exc:
                print(f'[model] retirement failed: {exc}', flush=True)
            finally:
                with self._load_cv:
                    self._retired_ids.discard(identity)
                    finished.set()
                    self._load_cv.notify_all()
        threading.Thread(target=retire, name=f'model-retire-{generation}', daemon=True).start()

    def _wait_for_retirements(self):
        with self._load_cv:
            self._load_cv.wait_for(lambda: all(event.is_set() for event in self._retirements))
            self._retirements.clear()

    def close(self):
        with self._load_cv:
            if self._service_closed:
                return
            self._service_closed = True
            previous = self._load_gen
            self._load_gen += 1
            self._predictor_ready.clear()
            self._retire_predictor_locked(self.predictor, previous)
            self.predictor = None
        self._wait_for_retirements()
        for raw in list(self._raw.values()):
            if hasattr(raw.get('frames'), 'wait_complete'):
                raw['frames'].close()
