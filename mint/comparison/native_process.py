"""One persistent CUDA context, isolated in the requested native environment."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--request',required=True);parser.add_argument('--kind',required=True)
    parser.add_argument('--owner',type=int,required=True);parser.add_argument('--reply-fd',type=int,required=True)
    args=parser.parse_args()
    def terminate(signum,frame): raise KeyboardInterrupt('Native GPU process terminated')
    signal.signal(signal.SIGTERM,terminate)
    if ctypes.CDLL(None).prctl(1,signal.SIGTERM)!=0 or os.getppid()!=args.owner:
        return 2
    reply=os.fdopen(args.reply_fd,'w',buffering=1)
    def send(value): reply.write(json.dumps(value)+'\n');reply.flush()
    try:
        from .worker import WorkerContext
        from .registry import root_for
        from .read_only import protect_native_roots,configure_native_cache
        ctx=WorkerContext(args.request)
        ctx.request['gpu_uuids']=[os.environ['CUDA_VISIBLE_DEVICES']]
        configure_native_cache(ctx.directory/'runtime')
        protect_native_roots([root_for('ace_kfree'),root_for('hawor_full')])
        os.chdir(ctx.spec['root']);sys.path.insert(0,ctx.spec['root'])
        import torch
        import cv2
        cv2.setNumThreads(1)
        if not torch.cuda.is_available(): raise RuntimeError('No CUDA device assigned')
        fraction=.8 if args.kind=='hawor_slam' else ctx.spec['memory_fraction']
        torch.cuda.set_per_process_memory_fraction(fraction,0)
        torch.set_num_threads(2)
        from .native_tasks import create_runner
        started=time.perf_counter();runner=create_runner(args.kind,ctx)
        send({'type':'ready','pid':os.getpid(),'model_load_seconds':time.perf_counter()-started})
        for line in sys.stdin:
            try:
                item=json.loads(line);ctx.check_cancel();started=time.perf_counter()
                yielded_before=ctx.timings.get('shared_gpu_yield_seconds',0.)
                torch.cuda.reset_peak_memory_stats()
                with torch.inference_mode(): value=runner(item)
                torch.cuda.synchronize()
                send({'type':'result','path':str(value),'seconds':time.perf_counter()-started,
                      'shared_yield_seconds':ctx.timings.get('shared_gpu_yield_seconds',0.)-yielded_before,
                      'peak_gpu_gib':torch.cuda.max_memory_allocated()/2**30})
            except Exception as exc:
                traceback.print_exc()
                send({'type':'error','error':str(exc),'retryable':isinstance(exc,torch.cuda.OutOfMemoryError)})
    except BaseException as exc:
        if isinstance(exc,KeyboardInterrupt):return 0
        traceback.print_exc()
        try: send({'type':'error','error':str(exc),'retryable':'out of memory' in str(exc).lower()})
        except (BrokenPipeError,OSError): pass
        return 2 if isinstance(exc,KeyboardInterrupt) else 1
    finally:
        reply.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
