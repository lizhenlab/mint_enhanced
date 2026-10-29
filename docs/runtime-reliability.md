# 运行可靠性、资源管理与恢复

默认保留全部源帧，以 `max_chunked` 逐窗推理。模型结构、预处理、相机拼接、手部平滑公式沿用原实现。
CLI、Viewer 和 Benchmark 共用 `mint/inference`；`eval/model_effect/inference` 保留兼容导入。
平滑参数显式区分 `production`（q/r/beta=0.6/0.6/2）和 `viewer`（0.7/0.5/0.3）预设。
CLI 使用 `--smoothing-preset`，页面通过“平滑预设”选择；两者仍可使用同一个推理内核。
`smooth` 与显式 `smooth@0.6,0.6,2` 采用同一个恢复身份。

## 任务与结果的身份

任务固定输入文件版本、视角、源帧索引、checkpoint 版本、配置和推理参数。
视频文件大小、修改/创建时间或 inode 变化后，会登记为新的输入；旧输入请求返回明确的 409。
恢复与批量提交同时校验磁盘权重、解析后的配置和已加载 predictor 的身份。同一路径更新权重或配置也会重建模型。
加载期间发生替换会拒绝发布该模型；任务恢复等待的是指定模型身份，加载失败会显示具体错误。
更换模型会发布新的 generation，取消旧请求，并在其当前窗口结束后在后台关闭旧调度器。
旧请求保留旧缓存字典，不会向新模型的缓存发布结果。

共享/空闲模式采用有界滚动窗口队列。快卡完成窗口后可继续领取后续窗口，输出仍按源顺序拼接。
单个异常或取消会移除未启动窗口，并等待当前 CUDA 窗口结束后释放资源。
原生 Streaming 的 KV 状态不能通过普通独立窗口恢复；长视频恢复使用 Chunked / Max Chunked。

## 断点恢复

原始窗口输出使用临时 NPZ、fsync 和原子替换写入 `.cache/window-predictions/.raw/<raw-id>/`；
`.cache/window-predictions/<task-id>/task.json` 保存完整任务参数、计数和状态。
原始缓存只保留模型、输入、具体窗口边界、预处理尺寸、精度设置与输出头；拼接及 UKF 参数保留在完整任务身份中。
因此改变 q/r/beta，或在相同切窗的 blend/smooth 之间切换，可复用网络输出，只重新拼接和平滑。
hard 模式的窗口范围不同，不能错误复用。旧版任务内的兼容窗口会在获得独占所有权后自动迁入共享原始缓存。

同一组原始窗口使用跨进程独占执行锁，所有者记录 PID、创建身份及进度心跳。
CLI 与网页重复提交时等待先前任务完成，再复用已保存窗口；等待可取消，不会覆盖所有者状态。
进程退出会由操作系统释放锁，下一次请求可接管并只计算缺失窗口。
恢复时核对身份、数组形状与有限值，损坏或不匹配的窗口重新计算，然后重新按序拼接和全序列平滑。
取消、失败、推理完成和最终输出状态记录到私有 task.json；服务重启后，失去进程的任务显示为 interrupted。

命令行以相同参数再次运行即可恢复；`--no-resume` 强制重算。
页面顶部“Tasks & recovery / 任务记录与恢复”可查看任务，点击“复用或恢复”恢复相同模型、视角和参数。
模型、配置或输入被替换后，旧任务不会假装恢复成功，应作为新任务提交。

批量推理单独选择 Full / Left eye / Right eye；选择会写入 manifest 和 NPZ。
已有结果仅在两份文件完整、来源和参数一致时跳过；匹配的残缺结果可修复，不匹配的文件提示输出冲突。
批处理使用一条预解码队列、共享 GPU 队列和有界渲染队列形成流水。

## 缓存与并发预算

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `MINT_FRAME_CACHE_MAX_GIB` | 256 | 解码 RGB 缓存总预算；部署机器可按磁盘余量配置 |
| `MINT_CACHE_MIN_FREE_GIB` | 16 | 磁盘空闲空间保护线 |
| `MINT_WINDOW_CACHE_MAX_GIB` | 16 | 窗口恢复数据预算 |
| `MINT_RENDER_CACHE_DIR` | `.cache/rendered-results` | 跨进程、跨重启的完整视频及合成导出索引 |
| `MINT_RENDER_CACHE_MAX_GIB` | 64 | 完整视频缓存预算 |
| `MINT_DECODE_PIPELINE` | 1 | Viewer 默认启用单视频解码与推理重叠；0 使用完整预解码 |
| `MINT_DECODE_PREFETCH_MIB` | 128 | 原生 RGB 预读目标；换算后限制在 32～512 帧，最多额外一个解码块 |
| `MINT_GPU_PREFETCH_WINDOWS` | GPU 数量×4 | 每次推理的窗口在途数量上限 |
| `MINT_VIEWER_CACHED_INPUTS` | 4 | Viewer 保留的非活动输入数预算 |
| `MINT_HTTP_WORKERS` | 4 | 活动重计算/渲染请求数量上限 |

缓存使用跨进程配额锁、空间预留和 LRU 回收；存活的 RGB 映射与窗口任务持有文件租约，回收不会删除正在使用的文件。
窗口缓存空间不足时记录警告、继续完整推理；RGB 空间不足会明确报错，不会截短视频。
同时最多运行两个导出任务；请求过多时返回 429 和可重试提示。
缓存来自原视频，目录保持私有。原始视频和模型文件均不属于缓存回收范围。

## 导出与计时

二维视频先写临时路径，编码器关闭成功且视频帧数正确后才原子发布。
渲染异常或取消会关闭编码器并清理临时文件；重试不会命中失败产物。
完整产物使用输入身份、已加载模型、推理设置、渲染参数和代码版本生成稳定 ID，并记录帧数和文件身份。
前端先调用 `/api/media/resolve/<kind>/<eid>`，再播放 `/results/<64位ID>.mp4`。
后者支持 Range 和浏览器私有缓存；旧的含 eid 的视频地址不允许浏览器缓存。过期页面的输入或模型上下文不符时返回 409。
新进程命中已验证产物后直接返回，不重新解码或渲染；损坏的缓存重新生成。
2D、固定世界、MuJoCo、重定向和多画面 MP4 合成都使用持久索引；视频下载期间持有缓存读租约。
CLI summary schema 2 的 `duration_seconds` 包括解码、加载、推理、预测写盘与视频编码；`stages` 给出阶段时间。
启用解码流水后，解码与推理的墙钟区间重叠，不能相加；`decode_pipeline.decode_seconds` 包含预读背压等待。
`decode_setup_seconds` 是创建解码生产者的前台耗时，`read_before_decode_complete` 可确认推理是否提前开始读取。
窗口预处理同时记录工作线程累计时间与实际覆盖的墙钟时间；滚动队列的 `forward_worker_seconds` 不能当作端到端耗时。

Benchmark 页面可显式选择 Shared、Idle only、Dedicated。资源策略进入测评记录和缓存身份，吞吐量只能在相同资源条件下比较。
Shared/Idle 遵守显存与负载监测。固定片段单次前向协议仍需足够显存，不会为通过测试而静默改变评测协议。

## 服务与环境

端口被占用时明确报错，不向占用者发送信号。
`python -m mint.service --state PATH --log PATH -- ./run_mint.sh viewer ...` 可监督自己创建的 Viewer 子进程，崩溃后有限次退避重启。
Viewer CLI 通过 exec 替换自身，监督器直接持有实际 Viewer 的 PID。
监督进程仅清理自己创建的会话中、UID 和 PID 创建身份匹配的成员；启动器先退出时，也会先回收所属残留进程再重启。
任务窗口检查点在进程退出后仍可恢复。

已有 `.venv` 的服务器可运行：

```bash
.venv/bin/python scripts/prepare_server_features.py --install --with-backends
.venv/bin/python scripts/prepare_data_assets.py
./run_mint.sh doctor --profile full --strict
./run_mint.sh train --check --config configs/training/mint_step2.yaml
./run_mint.sh pipeline --check
```

训练/数据/QA 依赖安装到独立 `.venv-features`，共享已有基础数值包，不替换推理环境。
`--with-backends` 同时注册仓库内的 GeoCalib/MoGe，并按固定提交与 SHA-256 安装其依赖。
服务器无法访问 GitHub 时，可用 `--backend-archives DIR` 提供已下载的两个固定源码归档；校验失败会停止安装。
`run_mint.sh train`、`pipeline` 和完整 doctor 自动选用该环境。
公开 GeoCalib、MoGe-2 和 MegaSaM 权重准备脚本保留来源与 SHA-256；权重不随源码上传。
完整数据生成还需要本地适配的 HaWoR、已编译的 SLAM 扩展及所选数据；完整训练需要相应数据和初始化权重。
doctor 会逐项报告缺项，`--inspect` 仅构建模型结构，不要求 Stage 1 初始化文件已就绪。

## 验证

```bash
python -m pytest -q tests eval/model_effect/benchmark/tests
```

回归包含跨模型缓存隔离、后台资源退休、文件替换、端口冲突、导出失败重试、批量视角、缓存租约、异构工作线程滚动调度、窗口中断恢复及服务崩溃恢复。
GitHub Actions CPU 回归不分发 MANO 资产；需要授权 MANO 文件的用例在实际部署机器验收。
