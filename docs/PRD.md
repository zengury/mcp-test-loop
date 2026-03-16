# PRD：MCP Test Loop（长周期自治测试闭环 Agent）

版本：v0.1（迭代基线）  
状态：Draft  
目标读者：机器人/系统软件工程师、现场运维工程师、产品/研发负责人  
仓库：`https://github.com/zengury/mcp-test-loop`

---

## 1. 背景与问题

机器人（或任何长周期设备）在进行**稳定性测试、姿态测试、耐久测试**时，往往需要持续运行数小时到数天，并在过程中根据外部数据不断调整策略。

典型例子：

- 机器人长时间行走：持续采集姿态/关节/温度/电源/告警事件，必要时调整速度、暂停、停止、执行恢复动作。
- 长周期任务（类比“德州烤肉 8–12h”）：基于温度、时间、状态事件进行周期性调参，并允许人工中断与修改策略。

传统做法依赖人工盯守或脚本化流程，缺少：

- 统一的观测数据抽象（不同子系统、不同 topic）
- 可控的指令下发安全边界（避免 LLM 直接执行危险命令）
- 可持续运行的闭环（NEVER STOP），并支持人工随时接管
- 可配置的测试步骤、策略与报告呈现

---

## 2. 产品目标（Goals）

### G1：长周期“Loop Forever”闭环
系统能持续按 tick（周期）运行，直到人工手动停止；任何单次失败不能导致系统整体停止。

### G2：严格可配置的测试步骤
每个 tick 的观测步骤由配置定义，并**严格按顺序**执行（runtime deterministic），避免依赖 LLM “自觉”调用工具。

### G3：安全、可审计的控制下发
LLM 不直接发布速度/执行命令；控制面通过 MCP server + preset allowlist；机器人侧由执行器节点完成“preset → 实际控制 topic publish”。

### G4：可操作的 UI
提供 UI 支持：

- 自动遍历 ROS2 topics（topic + type）
- 运行期配置 presets allowlist 与 preset→控制映射
- 展示 loop 测试报告（日志/图表/状态）

### G5：独立运行（不依赖完整 pi-mono）
使用精简 runtime（基于 `@mariozechner/pi-agent-core`），无需 pi 完整 TUI/会话系统即可运行。

---

## 3. 非目标（Non-goals）

- 不做通用“自动驾驶式”完全自主控制（必须有明确安全边界与 allowlist）。
- 不在 MVP 阶段实现复杂的消息类型 schema 自动生成表单（先 YAML 编辑 + discovery）。
- 不在 MVP 阶段实现多机器人集群调度与多租户权限体系。
- 不在 MVP 阶段将所有诊断/控制整合为单一 MCP server（控制与诊断保持分离）。

---

## 4. 核心概念与架构

### 4.1 两层循环的分工

**内层：pi-agent-loop（每个 tick 的一次推理回合）**

- 输入：本 tick 的观测数据（由 runtime 采集后拼接进 prompt）
- 输出：严格结构化 JSON 决策（strategy + rationale + optional act.preset）

**外层：LoopForever（长周期调度与容错）**

- 以固定 interval 调度 tick
- Tick 超时/错误时写日志 + backoff，继续下一轮
- 人工 steer 可在运行时注入新约束或中断

### 4.2 组件拆分（当前 repo）

1) **runtime（Node.js）**

- 功能：deterministic observe → LLM decision → deterministic act
- 依赖：`@mariozechner/pi-agent-core`, `@mariozechner/pi-ai`
- 输入：YAML 配置
- 输出：JSONL 日志（决策、动作、错误）

2) **motion-control MCP server（Python / MCP SSE）**

- 功能：`execute_preset(preset)` -> 发布 `std_msgs/String(preset)` 到 preset topic
- 安全：必须 `MANASTONE_ENABLE_CONTROL=true` 才执行；preset 必须在 allowlist
- 输出：MCP tool JSON response

3) **robot-side preset executor（Python / rclpy node）**

- 功能：订阅 preset topic，按 YAML mapping 发布到实际控制 topic（如 `/cmd_vel`, `/cmd_vel_stamped`）
- 支持：Twist / TwistStamped 示例；支持 `__now__` 时间戳宏

4) **operator UI（Python / Gradio）**

- 功能：topic discovery + 配置编辑 + loop report
- 输入：YAML 文件、JSONL 日志
- 输出：可视化与配置文件写入

---

## 5. 用户与使用场景（Use Cases）

### UC1：长时行走稳定性测试（典型）

- 运维人员配置 observe 步骤（系统状态、告警事件、关节状态、姿态等）
- 配置若干 preset：`stop`、`walk_slow`、`resume` 等
- Loop runtime 周期性运行：
  - 若出现 CRITICAL 或跌倒风险：决策为 STOP/HOLD，并下发 `stop` preset
  - 正常则继续并记录策略

### UC2：现场临时改策略（人工接管）

- 人工在 runtime stdin 输入 steer 文本，例如：
  - “接下来 10 分钟降低速度，观察温度变化”
  - “暂停执行任何动作，只记录观测与决策”
- 系统不中断主循环，下一 tick 生效。

### UC3：配置控制 topic 映射（/cmd_vel, /cmd_vel_stamped）

- UI 扫描 topic/type
- 用户编写 mapping：preset→publish Twist/TwistStamped
- executor 节点按 mapping 实际发布控制消息

---

## 6. 功能需求（Functional Requirements）

### 6.1 Loop Runtime（runtime/）

**FR-R1 配置加载**

- 从 `loop.yaml` 加载配置（YAML）
- 支持多 MCP server（SSE url）
- 支持 observe 步骤、act 配置、日志路径、超时/backoff

**FR-R2 Deterministic Observe（严格顺序）**

- 每 tick 按 `loop.observe[]` 顺序逐个调用 MCP tool
- 每步可配置 `timeoutMs`
- 输出写入本 tick 的 observations 结构（记录 label/server/tool）

**FR-R3 LLM 决策输出必须结构化**

- LLM 输出必须为“唯一 JSON 对象”，schema：
  - `strategy`: `CONTINUE|SLOW_DOWN|STOP|HOLD|ADJUST`
  - `rationale`: string
  - `act`: `{preset: string}` 或 null
- runtime 解析 JSON（允许 fenced ```json，但最终必须解析出对象）
- 解析失败：记录 error，并按 backoff 进入下一 tick（NEVER STOP）

**FR-R4 Deterministic Act**

- 若启用 `loop.act` 且 `decision.act.preset` 非空：
  - 调用控制 MCP server 的 tool（例如 `execute_preset`）
  - 可传 `dry_run`
  - 可配置 `timeoutMs`
- 结果写入 JSONL

**FR-R5 LoopForever 容错与永不停**

- 支持 `tickTimeoutMs`：整轮超时 abort，记 error，backoff 后继续
- 支持 `failureBackoffSec`：失败退避时间
- 成功后按 `intervalSec` 调度下一 tick

**FR-R6 人工 steer**

- stdin 输入普通文本：作为 steer message 注入 agent-loop
- `/stop` 终止 runtime

**FR-R7 日志与报告数据**

- JSONL 记录至少三类 entry：
  - `kind=decision`（含 observations metadata、decision、raw assistant）
  - `kind=act`（server/tool/args/result）
  - `kind=error`（错误信息）

### 6.2 MCP motion-control server（python/）

**FR-M1 Preset allowlist**

- presets 从 YAML 读取（例如 `motion_control_presets.yaml`）
- `list_presets` 返回可执行列表

**FR-M2 execute_preset**

- 参数：`preset: str`, `dry_run: bool`
- 若 `MANASTONE_ENABLE_CONTROL!=true`：返回 error（禁止执行）
- 若 preset 不在 allowlist：返回 error（含 available）
- mock_mode/dry_run：不发布，只返回将执行的信息
- 真执行：发布 `std_msgs/String(data=preset)` 到 preset topic

**FR-M3 ROS2 节点内执行**

- 在 server 内 lazy init rclpy node + publisher
- 使用线程 spin executor（不阻塞 MCP server）

### 6.3 Robot-side preset executor（python/）

**FR-E1 订阅 preset topic**

- topic：默认 `/mcp_test_loop/preset`（可 env 改）
- msg：`std_msgs/String`

**FR-E2 Mapping 执行**

- YAML mapping：`preset -> publish[]`
- 每个 publish：
  - `topic`: string
  - `type`: `<pkg>/msg/<MsgName>`
  - `msg`: dict（递归设置字段）

**FR-E3 支持 Twist / TwistStamped**

- MVP 映射示例覆盖：
  - `/cmd_vel` `geometry_msgs/msg/Twist`
  - `/cmd_vel_stamped` `geometry_msgs/msg/TwistStamped`

**FR-E4 时间戳宏**

- 支持 `header.stamp: {__now__: true}` 宏，写入 node 当前时钟时间

### 6.4 Operator UI（python/）

**FR-U1 Topic discovery**

- 使用 ros2 CLI（同 manastone discovery 思路）
- 展示 topic/type 列表
- 支持 filter（substring）与 limit

**FR-U2 配置编辑**

- 可编辑并保存：
  - presets allowlist YAML
  - preset mapping YAML
- 保存前做 YAML parse 校验

**FR-U3 Loop report**

- 从 JSONL tail N 行读取并展示
- MVP：JSON 展示
- 后续：图表/图标（见迭代）

---

## 7. 关键配置（Config）

### 7.1 runtime：loop.yaml（示例字段）

- `mcpServers.<name>.sseUrl`
- `loop.observe[]`: server/tool/args/timeoutMs/maxChars
- `loop.act`: server/tool/dryRun/timeoutMs
- `tickTimeoutMs`, `failureBackoffSec`
- `actionLog`

### 7.2 motion-control allowlist

- `python/config/motion_control_presets.yaml`

### 7.3 executor mapping

- `python/config/preset_topic_mapping.yaml`
- 需要覆盖 `/cmd_vel`、`/cmd_vel_stamped`

---

## 8. 安全与风控（Safety）

**S1 控制与诊断分离**

- 诊断 server 不做写入；控制 server 独立端口与可启停。

**S2 显式 enable 控制**

- `MANASTONE_ENABLE_CONTROL=true` 才可执行控制类 tool。

**S3 allowlist**

- MCP server 只允许 allowlist preset，防止 LLM 构造任意指令。

**S4 执行器可审计**

- preset→实际控制消息映射由 YAML 显式描述，可 code review / git 管理。

**S5 超时与失败退避**

- 单次 tick 或单步 observe/act 超时不会导致系统停止；必须退避继续。

---

## 9. 可观测性与报告（Observability）

**O1 JSONL 事件日志（runtime）**

- 决策、动作、错误均以结构化 JSONL 落盘
- UI 读取日志生成报告

**O2 MCP tool 结果留存**

- act 结果写入 JSONL，用于审计与复盘

**O3 后续指标**

- tick 成功率、失败类型分布、策略分布、动作频率
- 对 `/cmd_vel` 指令可加入“速度幅度统计”“累计运行时间”等

---

## 10. 里程碑与迭代计划

### v0.1（已作为基线）

- runtime：strict observe + JSON decision + optional act + tick timeout/backoff + JSONL
- motion-control MCP server：allowlist + publish preset topic
- executor：preset→publish mapping + Twist/TwistStamped + __now__
- UI：topic discovery + YAML 编辑 + JSONL report

### v0.2（配置更友好）

- UI 从 discovery 表格一键生成 mapping 模板（减少手工写 type/topic）
- UI 对 mapping 做更强校验（引用 preset 必须在 allowlist；type 格式校验）
- runtime 增加 “dry-run 全局开关”“暂停 act 只观测”模式

### v0.3（报告图表化）

- UI 图表：策略分布、失败率、tick 时间、动作次数
- 图标/状态卡：最近一次 CRITICAL、最近一次 STOP/HOLD、当前运行状态

### v0.4（更强的安全与扩展）

- 多动作序列（一个 preset 映射多个 publish，支持延时/持续时间）
- 速度限幅与约束（例如 Twist x,z 最大值）
- 与诊断事件系统更深整合（自动从 recent_events 提取严重级别规则）

---

## 11. 验收标准（Acceptance Criteria）

**AC1** 能在无人值守情况下运行 ≥ 2 小时，期间 tick 不会因单次错误停止。  
**AC2** 每 tick 的 observe steps 严格按 YAML 顺序执行，可在日志中验证顺序与耗时。  
**AC3** UI 能发现 `/cmd_vel` 与 `/cmd_vel_stamped` 并显示其 message type。  
**AC4** 配置 preset `stop` 后，触发 `execute_preset(stop)` 能让 executor 发布 Twist 全零到 `/cmd_vel`。  
**AC5** runtime 日志中能看到 decision/act/error 三类 JSONL entry，UI 能读取并展示 tail。  

---

## 12. 开放问题（Open Questions）

1) `/cmd_vel_stamped` 是否存在于目标系统？若不存在，mapping 可降级为 Twist。  
2) 观测侧（diagnosis MCP servers）最终统一为哪些？core/joints/imu/power/motion？每个 server 的稳定 SLA/timeout 需要实测。  
3) 是否需要“紧急停机硬路径”（绕过 LLM，event 检测直接触发 stop preset）？建议 v0.2 引入。  
4) 是否需要权限/认证（UI 与 MCP 控制 server）？现场网络环境决定。  

---

## 13. 附：约定俗成控制 topic（基线）

| Topic | Msg Type | 核心用途 | 典型场景 |
|---|---|---|---|
| `/cmd_vel` | `geometry_msgs/msg/Twist` | 下发线速度 + 角速度（笛卡尔空间） | 差速小车、无人车实时运动控制 |
| `/cmd_vel_stamped` | `geometry_msgs/msg/TwistStamped` | 带时间戳的速度指令 | 需同步 / 时序约束的高精度控制 |
