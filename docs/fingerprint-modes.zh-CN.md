# 多方案指纹内核：启动器与内核分层说明

本次采用用户确认的“兼容修正版”，方案版本为 `compatible-v1`。基底是已交付 Google 服务及原生 Passkey 修复的 Windows Chromium `155.0.8059.39`，源提交 `90e6d29bf81408820c7bba89caf8bad7dec8430c`。

最终交付一个内核，按参数独立选择已有或新增方案。用户已明确将 Passkey 延迟优化留到以后，因此本次不纳入 `a493096` 的优化补丁；这里的缓存续编改进只用于节省构建时间。

这里记录实现和验收契约。完整编译、浏览器实测、物理 GPU 验收分别以构建产物内的报告为准；不能由补丁应用成功或算法测试通过推断它们已经完成。

## 启动器层

本次没有修改管理器界面、已有环境数据、Cookie、代理、固定种子、活动安装包或每个环境的内核绑定。管理器可以随后按能力清单接入新选项。

浏览器启动入口新增独立模式参数，统一归一化为原有配置通道中的 `uxr-*` 键，再传给子进程。**没有传新模式参数时，仍走原 Chromix 分支**；原有补丁系列、默认值和旧参数保留。某一项选择“原生”，只覆盖该项，不能隐式全局关闭其它指纹方案。

| 参数 | 可选方案 |
|---|---|
| `fingerprint-canvas-mode` | native / chromix / shardx / cloak |
| `fingerprint-webgl-mode`（像素读回） | native / chromix / shardx / cloak |
| `fingerprint-fonts-mode` | native / chromix / shardx / cloak |
| `fingerprint-audio-mode` | native / chromix / shardx / cloak |
| `fingerprint-text-mode` | native / chromix / shardx / cloak |
| `fingerprint-rects-mode` | native / chromix / shardx |
| `fingerprint-offset-mode` | native / chromix / cloak |
| `fingerprint-sensors-mode` | native / chromix / shardx |
| `fingerprint-media-mode` | native / chromix / shardx |
| `fingerprint-voices-mode` | native / chromix / cloak |

公共参数均使用 `--参数=值`。各项独立种子为 `fingerprint-<项目>-mode-seed`，与旧 `canvas-seed`、`audio-seed` 区分；省略时按约定继承已有固定种子。显式 ShardX 种子范围为 `1..4294967295`，继承更大旧种子时使用既有折叠规则。显式选择 `chromix` 保留原先各项种子的优先级。非法模式或没有模式的专属参数会报错，不静默换方案。

UA、CPU、内存和屏幕分别由 `fingerprint-identity-mode`、`fingerprint-cpu-mode`、`fingerprint-memory-mode`、`fingerprint-screen-mode` 选择 `native/custom`。CPU 支持 `1..128`，内存支持 `0.25..32 GiB`，新内存模式将 JavaScript 和两种 Device-Memory 请求头统一到同一组桶值；不会改变实际可分配内存。

`fingerprint-hardware-profile=cloak` 按种子选择已恢复的硬件元数据：Intel 18 项、NVIDIA 41 项、Apple 20 项，共 79 个逻辑项、67 个不同名称。支持指定厂商和池内索引；用户明确配置的 CPU、内存、GPU 字符串及单项原生选择优先。该池不是显卡模拟器。

GPU 名称、像素噪声、能力集合分开选择。GPU 总身份模式及 WebGL/WebGPU 各自身份模式支持 `native/custom/cloak`。WebGL 能力支持 `native/custom`；WebGPU features 支持 `native/custom/cloak`，limits 支持 `native/cloak`。完整参数、数值范围和覆盖规则见[机器可读能力清单](../build/fingerprint-modes-capabilities.json)及[详细接口说明](fingerprint-modes.md)。

补齐的独立控制：

- `fingerprint-audio-default-sample-rate`：`3000..768000 Hz`，只在网页未给 AudioContext 指定采样率时改变真实渲染采样率和 Worklet 时钟；显式参数优先。
- `fingerprint-audio-channel-limit`：`1..32`，与后端真实声道能力取较小值，并同时约束 setter。
- `fingerprint-audio-default-channel-count`：`1..32`，真正配置初始输出 bus；超出实际能力时构造报错，不伪报支持。
- `fingerprint-fonts-allowlist` 与 `fingerprint-fonts-local-allowlist`：分别限制普通字体访问和额外的本地访问，均与选定字体方案求交集。省略不新增限制；显式空串拒绝对应范围的本地字体。它们不是原版假字体声明参数的等价替代。
- `fingerprint-webgl-masked-vendor/renderer`：真实硬件上下文 custom 身份下成对设置 `GL_VENDOR/GL_RENDERER`；软件/回退上下文保留原生标准值。

## 内核层

新实现拆成六组叠加补丁：公共配置与硬件池、图形、其它表面、GPU 能力、真实字体白名单、音频默认值。组合时保留原 231 项选中基础补丁以及 Google 服务、AppID、原生 Passkey 修改。共享 WebGL 文件按图形、GPU 能力补丁顺序应用；字体白名单也是前置字体补丁之后的明确增量，均按顺序重放，不能分别重复套用。

| 项目 | 兼容修正及与原版的区别 |
|---|---|
| Canvas / WebGL | 保留恢复出的种子算法，修正裁剪坐标与透明度处理。ShardX WebGL 重复读取保持稳定，不复刻每次调用递增导致的变化。Cloak 原来的固定图片替换会抹去不同绘制内容，兼容版不启用该分支。 |
| WebGL 能力 | 只支持与真实验证缓存共同生效的五个尺寸上限；取真实值与配置值的较小值。扩展只返回真实支持集合的子集，拒绝未接入执行验证的数值参数。 |
| WebGPU | 恢复身份及 36 项限制的配置逻辑，但 features 始终与真实适配器求交集；不跳过设备创建检查，不把实际设备 limits 改回虚假值。软件或回退适配器保留真实身份。 |
| 字体 | ShardX 对最终解析到的字体族过滤，避免大小写/别名绕过。Cloak 使用 Windows 30/138/149/231/232、macOS 562、Linux 47 候选表与真实字体结合；不伪造未安装的字体文件，保留必要字形回退。 |
| 文字/布局 | ShardX rect/text 与 Cloak offset 分别选择。Cloak TextMetrics 参考修正需通过文本、字体、平台、全部 glyph 字体族及真实边界检查，否则原生返回。 |
| 音频 | ShardX 保留低位算法，但只处理实际写入范围，保护调用方尾部并正确处理重叠数组。另补默认采样率、真实输出声道与能力上限，沿用 Chromium 渲染和重采样。Cloak 仅在 macOS 人设、单声道 44.1 kHz 且 PCM 与三个已记录原生样本逐位一致时替换；不是通用 macOS 音频引擎。 |
| 传感器 | 只处理真实加速度系列读值，不制造硬件。新增 Auto 默认偏移为 `0.1 m/s²`，显式零仍为零；旧模式默认值不变。 |
| 媒体设备 | 对真实音频输入、音频输出、视频输入分别设置 `0..32` 枚举上限；保留真实 ID、权限标签和能力关联。不创建虚拟采集设备，也不声称能阻止使用已知 ID 捕获。Cloak 无后端的假设备占位不纳入兼容版。 |
| 语音 | 只按参考表筛选、排序真实库存，保留实际 URI、默认标记和本地/远程引擎属性；不宣称缺失的 Google/macOS 语音引擎已安装。 |

未确认的字体 coverage/别名分支、完整 DSP 图行为、双 destination 音频分支及未观察到的传感器机制，仍明确列为未知或不支持。此前研究有这些边界，因此本次也不会声称已完整还原闭源内核的每一条路径。

## 构建、缓存和验收

首次续编明确使用完整检查点 `39396862771`，绑定原提交、补丁、准备配方、GN 参数及全部分卷身份。已成功候选包 `39425901213` 保留作基线；候选压缩包不能替代含源码和编译对象的检查点。

2026-10-20 补充修复从指纹构建 `39498745053` 的完整第一阶段检查点续编，关闭 bootstrap。此次只修正 custom WebGL 能力模式下普通与多采样 renderbuffer 分配缺少上限校验的问题；未选择该模式的入口保持原行为。Cloak 的本机 GPU/像素边界复核没有发现新的专属必修项，原有兼容范围保持不变。

音频时钟验收协议 v2 先暂停 AudioContext，完成 Worklet 图连接后恢复，收集384个连续回调。采样率与时间比例、严格相邻渲染量子分别记录；任何跳量子仍使严格检查失败，没有放宽原判据。旧失败报告保留，新报告绑定自己的 harness/module 摘要；这项测试调整不改 DSP，也不自动开放启动器参数。修复的完整编译及新二进制验收仍以本轮构建产物为准。

新分支与构建产物使用独立的 `fingerprint-modes-win155` 标识。每阶段恢复一次；内容未变的源码保留 mtime；只有构建图依赖变化才刷新 Ninja 元数据。成功上传已验收候选后，跳过昂贵的全源码/对象重新归档。阶段失败或预算用完时先完整保存分卷，最后提交 manifest，再允许后续续编。

验收保留原 Google 和 Passkey 测试，增加真实生产 helper 的原生测试及原生/旧模式/混合模式浏览器矩阵，覆盖新增默认值、名单和主要策略的正向场景。报告绑定实际 EXE、DLL、能力清单和测试结果哈希。GPU API、字体、语音库存或参考 PCM 不满足条件时单独标为未测试；不能把跳过、回退原生或负向场景通过等同于该策略正向验收通过。

构建进程每约一分钟记录同一 runner 的 CPU、内存、磁盘、Ninja 子进程活动和新增日志。当前聊天的定时监控每十分钟核对精确运行号与提交；仅在可处理失败、修复、完成或需要用户操作时通知。它不会取消已有编译、重复派发相同源码或自动切换生产环境内核。
