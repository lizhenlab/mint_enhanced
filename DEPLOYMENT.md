# mint_enhanced 部署与验证记录

本仓库基于 [wuji-technology/wuji-ego-mint](https://github.com/wuji-technology/wuji-ego-mint) 的
`1bd21e4edd6d5d2d59789ce64ee5450de24a7c2b`，保留上游 Git 历史、MIT 许可证和第三方声明。
2026-10-29 核对时，已部署服务器的全部 3315 个受 Git 管理文件与该提交一致。
本次整理补入已有启动脚本、依赖版本记录和操作说明；核心推理、训练和 Viewer 代码保持该官方版本。

## 已有服务器环境运行

`run_mint.sh` 使用项目目录下的 `.venv/bin/python`，自动切换工作目录，并将缓存及临时目录放在项目内。
它为无界面渲染设置默认 `MUJOCO_GL=egl`，并将 OMP、MKL 线程数默认设为 8；这些值可通过环境变量覆盖。

启动 Viewer：

```bash
./run_mint.sh viewer --host 127.0.0.1 --port 8011 \
  --devices 0 --no-open --max-frames 450
```

在页面中选择 `checkpoints/model.safetensors` 并点击加载模型，再在右侧浏览服务器视频、点击开始推理。
右侧输入浏览器使用服务器文件路径。

单次命令行推理先使用短片：

```bash
./run_mint.sh infer --input /absolute/path/short_video.mp4 \
  --checkpoint checkpoints/model.safetensors \
  --output artifacts/my_video --devices cuda:0 --max-frames 64
```

Mac 建立 SSH 隧道并保持连接：

```bash
ssh -N -L 18011:127.0.0.1:8011 USER@SERVER
```

将 `USER@SERVER` 替换为实际登录地址，浏览器打开 <http://127.0.0.1:18011/>。
`450` 是当前部署使用的临时帧数上限，需要在启动参数中显式传入；启动脚本没有将其写成全局默认值。

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
  --devices 0 --no-open --max-frames 450
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

## 视频输入与当前内存限制

当前版本会先解码并缓存所选视频帧，再把整段原始分辨率帧转成浮点数，之后才缩放。
`Max Chunked` 对后续 GPU 推理分窗，不会限制此前的 CPU 内存占用。
因此，高分辨率、长视频应先缩小分辨率并分段；450 帧只是临时部署限制，不能保证任意分辨率的视频都能处理。
本仓库本次上传没有修复该内存分配方式。

对于左右并排的双目输入，应先选择一个视角。以下命令提取左眼、前 15 秒并缩小至 640 像素宽；
仅在输入确实是等宽左右双目布局时使用：

```bash
ffmpeg -i /absolute/path/stereo_video.mp4 -map 0:V:0 -t 15 \
  -vf 'crop=iw/2:ih:0:0,scale=640:-2,fps=30' \
  -an -c:v libx264 -pix_fmt yuv420p -movflags +faststart \
  /absolute/path/left_15s.mp4
```

## 已完成验证与边界

- 官方短样例：64 帧推理、相机及双手预测有限值检查、世界坐标手部几何、二维叠加 MP4 导出通过。
- 2026-10-29：从左右双目高分辨率源片提取左眼，制作 640×486、30 FPS、450 帧短片，Viewer 推理与二维叠加导出通过。
- 长视频原片未完整重跑；上述验证不代表重建精度评测，也不代表训练、全部数据后端或所有仿真导出均完成验证。

模型权重、MANO、用户原始视频、运行输出、日志、虚拟环境和缓存未纳入此次代码上传。
官方仓库原有的文档媒体及公开示例保留在上游历史中；第三方代码与资产遵循各自条款，参见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
