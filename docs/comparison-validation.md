# 统一比较平台验收记录

实现与运行说明见 [comparison-platform.md](comparison-platform.md)。本次在独立 worktree 和 8024 / Mac 18014 端口完成开发验收，保留正式 8011 服务，之后再部署到正式端口。MINT 推理核心文件未修改；HaWoR 和 ACE 继续从原目录、原环境加载。

## 自动回归

服务器 CPU 回归：`171 passed`。覆盖既有 MINT、Viewer、Benchmark 与新比较平台；新增检查包括输入身份、全帧时间表、无效几何与相机、不可变结果、文件变化、缺失帧、相机组合、指标范围、并发去重、取消恢复、持久队列、独立 CPU 导入槽位、磁盘导出与配额、实际视频解码及缓存复用。

GitHub CPU CI 使用相同测试集，排除需要本地许可 MANO 文件的资产定位测试。JavaScript 通过 Node 语法检查，并另做真实浏览器操作。

## 真实原生运行

| 用例 | 验收结果 |
|---|---|
| HaWoR 官方 `video_0.mp4`，121 帧，同输入 MINT / HaWoR Full / ACE K-free | 三者均完成 121 帧；HaWoR 检测、手估计、SLAM、Metric3D 尺度和补全均成功 |
| 720 帧双目样例，左眼，同输入三算法 | 三者均完成全部 720 帧；未按 450 帧截断 |
| 81 帧，ACE K-given + HaWoR Camera only | 两者完成；标定明确标为验收用假定值，不作为真实 GT |
| ACE `native_tiled`，输入 118 帧 | 原生输出 117 帧，`partial`，最后一个样本位置返回缺失，不重复最后结果 |
| 720 帧 ACE 独立片段中取消并恢复 | 只取消目标 run；恢复复用 1 个完整片段，最终 720 帧 |
| 私有验收 coordinator 意外退出 | 核对父子 PID 与启动时间，在仅完成 1 个片段时终止自己的 coordinator；worker 被回收，重新启动后复用 1 片段并完成 720 帧 |
| MINT NPZ / HaWoR 世界 NPZ / ACE PKL 导入 | 三种原生格式均完成解码与标准发布；HaWoR 明确给出原始 1920×1080 网格 |
| 标准参考导入 | 合成几何协议用例通过，明确标为合成测试，不作为真实精度评测 |

意外退出测试最初一次没有达到片段复用断言，未计入通过；在新的私有目录中增加父子 PID 和“尚未完成全部片段”的前置校验后，完整复测通过。测试没有终止正式 Viewer 或其他 GPU 任务。

## 几何与投影对照

使用各自原生环境，独立重建保存的原生结果，再与统一协议的几何、K、相机及时间表对照。CPU 线程与浮点舍入可能造成微小差异，验收阈值预设为几何 `1e-6 m`、投影 `0.002 px`。

| 对照 | 最大关节差异（米） | 最大投影差异（像素） |
|---|---:|---:|
| MINT 原生 MANO 与统一相机系关节 | 1.1921e-7 | 0.0001555 |
| HaWoR 原生世界关节，经统一相机往返 | 1.8284e-7 | 0.0003166 |
| ACE 原生片段合并、MANO 与统一关节 | 5.9605e-8 | 0.0001344 |

MINT 与 ACE 的 K 对照差异为 0，MINT 相机外参差异为 0；三者源帧号与 PTS 时间表逐项一致。HaWoR 世界往返最大误差 `1.4734e-7 m`。示例中 ACE 解码腕点与 MANO 平移参数相差最高约 `0.0988 m`，确认平台必须使用解码后的腕点。

这些是适配正确性检查，不是模型精度结果。测试输入没有三算法共用的真实 GT，没有据此给模型排名。

## API 与导出

- 重复提交相同输入/代码/权重/参数复用同一 run。
- ACE + MINT camera、ACE + HaWoR camera 两个组合均通过，手部相机系关节及 K 与原 ACE 数组逐元素相同。
- 同样本原生结果可追加；不同样本的结果被拒绝追加。
- 缺失尾帧返回 `available=false`、坐标 `null`。
- 输入 MP4 的 HTTP Range 返回 206，验证范围为 `bytes 0-255`。
- 几何 ZIP 包含逐帧关节、顶点、相机、有效性与完整来源清单，使用磁盘生成。
- 同步视频导出实际得到 H.264、yuv420p、1920×418、30 FPS、121 帧，编码约 4.42 秒。

主要记录位于服务器 `/data1/lizhen_algo/mint/.deployment/`：

```text
comparison-baseline-20261029/baseline.json
comparison-qa.log
comparison-acceptance-ids.json
comparison-release-ids.json
comparison-numerics/*.json
comparison-cancel-resume.json
comparison-recovery-acceptance.json
comparison-api-acceptance.json
```

可直接打开的验收记录：

- 三算法：`/compare?id=b8cf527dfda94e3a99113bd55449e6ef`
- 720 帧完整序列：`/compare?id=3ca8542b814f4ebe9d8acddd8c532b65`
- 原生导入与组件组合：`/compare?id=6df97dc560744a77882b7a3bf762477f`

## 验证范围

当前新增平台已完成 121/720 帧、各原生模式、导入、恢复与导出的实际验证。原 MINT 在此前版本已完成 10,428 帧全视频验收；这次未重新把同一 10,428 帧视频完整跑过 HaWoR 和 ACE。更长视频仍受原生 SLAM、VAE 模式、RAM、显存、磁盘和配置配额约束，平台明确报出失败或部分结果。独立 81 帧模式与整段 VAE 不能视为数值等价，组件相机组合也不能当作原生联合预测。
