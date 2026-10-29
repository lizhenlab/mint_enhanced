# mint_enhanced 部署与验证记录

本仓库基于 [wuji-technology/wuji-ego-mint](https://github.com/wuji-technology/wuji-ego-mint) 的
`1bd21e4edd6d5d2d59789ce64ee5450de24a7c2b`，保留上游 Git 历史、MIT 许可证和第三方声明。
2026-10-29 最初导入时，已部署服务器的全部 3315 个受 Git 管理文件与该提交一致。
本仓库保留已有启动脚本、依赖版本记录和操作说明，并在推理运行时增加了[动态 GPU 共享调度](docs/gpu-scheduling.md)。
模型结构、权重和窗口拼接逻辑沿用该官方版本。

## 已有服务器环境运行

`run_mint.sh` 使用项目目录下的 `.venv/bin/python`，自动切换工作目录，并将缓存及临时目录放在项目内。
它为无界面渲染设置默认 `MUJOCO_GL=egl`，并将 OMP、MKL 线程数默认设为 8；这些值可通过环境变量覆盖。

启动 Viewer：

```bash
./run_mint.sh viewer --host 127.0.0.1 --port 8011 \
  --devices shared --no-open --full-max-frames 32
```

在页面中选择 `checkpoints/model.safetensors` 并点击加载模型，再在右侧浏览服务器视频、点击开始推理。
右侧输入浏览器使用服务器文件路径。

单次命令行推理先使用短片：

```bash
./run_mint.sh infer --input /absolute/path/short_video.mp4 \
  --checkpoint checkpoints/model.safetensors \
  --output artifacts/my_video --devices shared --max-frames 64
```

Mac 建立 SSH 隧道并保持连接：

```bash
ssh -N -L 18011:127.0.0.1:8011 USER@SERVER
```

将 `USER@SERVER` 替换为实际登录地址，浏览器打开 <http://127.0.0.1:18011/>。
服务默认处理全部源帧，不设每个视频总帧数上限；`--full-max-frames 32` 只控制单次模型窗口。

## 新机器准备

支持平台和完整安装步骤见 [官方安装说明](docs/installation.md)。需要 Linux x86_64、Python 3.10、兼容的 NVIDIA 驱动和 FFmpeg。
已验证服务器使用 Python 3.10.12、PyTorch 2.8.0+cu128。

没有现成 `.venv` 时，可以使用官方 Conda 安装入口：

```bash
bash scripts/create_env.sh inference
conda activate mint-inference
python -m mint doctor --profile inference --strict
bash scripts/download_assets.sh
```

此方式安装后，在激活的 Conda 环境中直接使用：

```bash
python -m mint viewer --host 127.0.0.1 --port 8011 \
  --devices shared --no-open --full-max-frames 32
```

渲染手部网格和骨架还需要自行准备已授权的 MANO 文件：

```text
assets/mano/mano_right/MANO_RIGHT.pkl
assets/mano/mano_left/MANO_LEFT.pkl
```

官方权重下载脚本只下载 MINT 模型；MANO 需要按其官方条款单独获取。
公开模型的 SHA-256 记录在 [deployment/source.json](deployment/source.json)。

[deployment/requirements-torch.txt](deployment/requirements-torch.txt) 和
[deployment/requirements-inference.txt](deployment/requirements-inference.txt) 是服务器使用的直接依赖记录；
[deployment/requirements-server-snapshot.txt](deployment/requirements-server-snapshot.txt) 是完整环境的版本快照，已去掉本机绝对路径和 editable 安装项。
这些文件用于排查环境差异，安装入口仍以官方安装说明为准。
服务器上的 Decord 0.6.0 使用了本地修复 wheel：修正兼容性标签并重新生成 RECORD，未改变可执行文件内容；原包与修复包哈希保留在来源记录中，二进制 wheel 未上传。

## 完整长视频

采用分块解码、RGB 磁盘映射和逐窗预处理处理完整视频，详见 [长视频说明](docs/long-video.md)。
裸视频默认保留源帧率与所有源帧。普通单目选择 Full，等宽左右双目在前端选择 Left eye 或 Right eye。
原片保留不变；左右眼分别缓存、推理和导出，时间范围不截短。
当前服务器有足够磁盘空间供原始双目视频的完整单眼 RGB 缓存使用。

## 已完成验证与边界

- 官方短样例：64 帧推理、相机及双手预测有限值检查、世界坐标手部几何、二维叠加 MP4 导出通过。
- 2026-10-29：从左右双目高分辨率源片提取左眼，制作 640×486、30 FPS、450 帧短片，Viewer 推理与二维叠加导出通过。
- 长视频原片未完整重跑；上述验证不代表重建精度评测，也不代表训练、全部数据后端或所有仿真导出均完成验证。

模型权重、MANO、用户原始视频、运行输出、日志、虚拟环境和缓存未纳入此次代码上传。
官方仓库原有的文档媒体及公开示例保留在上游历史中；第三方代码与资产遵循各自条款，参见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
