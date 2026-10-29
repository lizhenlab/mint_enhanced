# 动态共享最多 8 张 GPU

命令行推理和交互 Viewer 默认使用 `--devices shared`：从当前进程可见的 CUDA 设备中选择最多 8 张卡，在显存及计算余量允许时与其他任务共享，并持续监测负载、自动加入或退让。
严格只使用空闲卡时，改为 `--devices idle`。
使用原有固定设备方式时，仍可传入 `--devices auto`（全部可见卡）、`--devices 0,1` 或 `--devices cpu`。

```bash
./run_mint.sh viewer --host 127.0.0.1 --port 8011 \
  --devices shared --no-open --full-max-frames 32

./run_mint.sh infer --input /path/to/short_video.mp4 \
  --checkpoint checkpoints/model.safetensors \
  --output artifacts/prediction --devices shared
```

## 调度行为

1. 模型权重先加载到 CPU。仅在有待处理工作且 GPU 通过检查时，才在该卡建立模型副本。
2. 默认每 5 秒检查 GPU 进程及显存，使用 `nvidia-smi pmon` 读取其他进程的计算负载，排除 MINT 自身负载。创建副本前额外刷新一次。共享模式下，其他进程合计负载不超过 35% 且显存预算不少于 16000 MiB 时可加入；严格空闲模式仍跳过全部其他计算进程。
3. 每卡一个串行工作线程，共享待处理队列。分窗模式的独立窗口可分发到最多 8 张 GPU；空闲卡释放后，自动领取尚未开始的工作。
4. 共享卡上按默认 30% 工作占空比主动停顿；每处理一个窗口，按该窗口用时计算下一段让出时间。空闲卡不执行这段停顿。所有卡都超过负载或显存阈值时等待，可从前端停止。
5. 共享模式检测到外部负载或显存压力超过阈值后，完成当前窗口并释放模型及缓存，等待恢复余量。严格空闲模式在出现任何外部计算进程后退让。CUDA 上下文本身可能仍占少量显存。
6. 窗口输出回到 CPU 后按输入顺序拼接。因此 GPU 完成顺序不会改变相机轨迹与手部窗口的拼接顺序。

没有工作可领时，模型副本保留短暂的 30 秒复用期，之后自动释放，避免空闲服务长时间占用多张卡的模型显存。

`full` 和 `streaming` 模式的单段前向需要同一模型连续执行，使用一张通过检查的 GPU；跨卡并行主要用于 `chunked`、`max_chunked` 的独立窗口。
只有一个窗口或剩余工作少于可用 GPU 数时，工作卡数会相应减少。
批量视频使用同一调度器进行 GPU 推理，并保留原有 CPU 渲染和写盘流水线。
动态模式同时将轻量的 MANO 几何解算默认设为 CPU，避免渲染阶段另外占用默认 GPU 0。共享模式将 MINT 的 PyTorch 显存预算限制为单卡总显存的 30%（40 GiB 卡约 12 GiB）；CUDA 上下文和部分驱动分配不属于该分配器预算。

调度器按 GPU UUID 对齐 CUDA 逻辑编号与 `nvidia-smi` 物理设备，遵守 `CUDA_VISIBLE_DEVICES` 的可见范围及重新编号。
默认使用前 8 张可见设备作为候选。需要限制卡范围时，可以在启动前设置该变量，例如：

```bash
CUDA_VISIBLE_DEVICES=2,3,5,7 ./run_mint.sh viewer --devices shared \
  --host 127.0.0.1 --port 8011 --no-open
```

此时状态中的 `cuda:0` 对应可见列表中的第一张卡，具体物理 UUID 也会显示在状态接口中。
同一用户启动的多个 MINT 实例通过文件锁协调占用；外部程序可以独立申请 GPU，监控和释放发生在检测后及当前窗口边界。
遥测失败、信息过期、UUID 不匹配或显存不足时停止分发并等待后续检查。当前动态模式不支持 MIG、FP8 或 `torch.compile`；需要后两项时使用固定设备模式。

## 可配置参数

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `MINT_GPU_MAX_WORKERS` | `8` | 最大候选卡数，范围 1–8 |
| `MINT_GPU_POLL_SECONDS` | `5` | GPU 监测间隔，单位秒 |
| `MINT_GPU_MIN_FREE_MIB` | 共享 `16000`；空闲 `12000` | 最小显存预算，单位 MiB；检查时将本实例已用显存加回，避免将自己判断为忙碌 |
| `MINT_GPU_IDLE_RELEASE_SECONDS` | `30` | 无工作时保留副本的秒数，超时释放模型及缓存 |
| `MINT_GPU_MEMORY_FRACTION` | `0.30` | 共享模式中，本实例 PyTorch 分配器的单卡显存上限比例 |
| `MINT_GPU_SHARE_UTILIZATION` | `35` | 允许共享的其他进程合计 SM 负载百分比上限 |
| `MINT_GPU_SHARE_DUTY_CYCLE` | `0.30` | 共享卡的主动工作占空比，范围大于 0 且不超过 1 |

严格空闲模式在未持有本实例显存时，还要求整体利用率不超过 10%。动态模式默认每张卡一次处理一个窗口，并将默认窗口上限设为训练窗长；显存预算不足以保证任意模型、分辨率或手动窗口配置都能运行。
显存不足时会释放该副本、短暂冷却，并尝试其他候选卡；同一工作在全部候选卡上失败后返回错误。

```bash
MINT_GPU_POLL_SECONDS=3 MINT_GPU_MIN_FREE_MIB=16000 \
  ./run_mint.sh viewer --devices shared --no-open
```

## 查看状态与停止

页面“推理策略”区域持续显示可用卡数、正在工作卡数和排队窗口数。没有满足阈值的卡时显示等待提示；鼠标悬停可查看各卡状态。
HTTP 接口：

- `GET /api/gpu/status`：候选 GPU、UUID、其他进程 PID、可用显存、已加载副本、运行和排队状态。
- `GET /api/model_ready`：保留原字段，并增加 `gpu_scheduler`。

模型“就绪”表示 CPU 权重可用；此时仍可能等待 GPU 负载或显存余量。等待期间可以点原有停止按钮。
更换权重会关闭旧调度器，等待当前窗口结束并释放旧副本，随后为新模型建立调度器。

## 内存边界

长视频已改为分块解码、RGB 磁盘映射和逐窗预处理，默认不截断视频。详见 [完整长视频处理](long-video.md)。
左右双目视频应先选取一个视角，参见 [部署说明](../DEPLOYMENT.md)。

## 验证

无 GPU 的调度行为测试：

```bash
python -m unittest discover -s tests -p test_gpu_scheduler.py -v
```

测试使用模拟遥测覆盖 8 卡并行、忙卡跳过、新空闲卡加入、已有外部任务退出后的恢复、取消、显存错误重试、输出顺序和监控失败。
2026-10-29 在 A800 40 GiB × 8 的服务器上完成共享模式验证：其他进程仍在运行时，256 帧、640×486 视频的 9 个窗口由全部 8 张卡参与处理，工作线程峰值为 8，HTTP 返回 200；505 个有效手部帧均为 21×3 且坐标有限，结束后队列为空。相关 CPU 回归测试共 25 项通过。
模拟测试不代表真实 8 卡性能评测。共享计算可能增加其他任务时延，策略通过显存预算、工作占空比和负载监测减少干扰，无法保证其他任务完全不降速。

遥测字段与可见设备语义参考 [NVIDIA nvidia-smi 文档](https://docs.nvidia.com/deploy/nvidia-smi/) 和 [PyTorch CUDA 环境变量](https://docs.pytorch.org/docs/stable/cuda_environment_variables.html)。
