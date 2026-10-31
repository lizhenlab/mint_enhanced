"""Real NCCL all-reduce acceptance, using the launcher's already leased GPUs."""
import argparse
import os
from pathlib import Path


def main():
    import torch
    import torch.distributed as dist
    from datetime import timedelta
    from mint.runtime import atomic_json
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True); args = parser.parse_args()
    local_rank = int(os.environ['LOCAL_RANK']); torch.cuda.set_device(local_rank)
    dist.init_process_group('nccl', timeout=timedelta(seconds=90))
    try:
        rank, world = dist.get_rank(), dist.get_world_size()
        value = torch.tensor([float(rank + 1)], device='cuda'); dist.all_reduce(value)
        if value.item() != world * (world + 1) / 2:
            raise ValueError('Distributed all-reduce did not include all ranks')
        atomic_json(Path(args.output) / f'smoke-rank-{rank}.json', {'rank': rank, 'world_size': world,
            'sum': value.item(), 'gpu': torch.cuda.get_device_name(local_rank), 'backend': 'nccl'})
    finally:
        dist.destroy_process_group()


if __name__ == '__main__':
    main()
