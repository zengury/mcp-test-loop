# 配置与运行指南（mcp-test-loop）

本项目由三部分组成：

1) **Python 侧 MCP Servers（观测 + 控制）**：`python/`
2) **Robot-side 执行器（将 preset 翻译为传统 ROS2 控制 topic publish）**：`python/` 内同一包
3) **Loop Runtime（LoopForever，Node.js）**：`runtime/`

> 要求：Python >= 3.10。

---

## 1. 安装

### 1.1 安装 Python 包（观测/控制/UI/执行器）

```bash
cd python
pip install -e .

# 真机 DDS 模式可选：
# pip install -e ".[dds]"
```

### 1.2 安装 runtime（Node loop）

```bash
cd runtime
npm install
```

---

## 2. 关键约定（控制 topic）

### 2.1 preset topic（意图下发）

控制 MCP server 会把 **preset 名称**发布到一个 ROS2 topic：

- Topic：`/mcp_test_loop/preset`
- Type：`std_msgs/msg/String`
- 内容：`data=<preset_name>`

可通过环境变量覆盖：
- `MANASTONE_MOTION_CONTROL_TOPIC`（motion-control 发布用）
- `MANASTONE_PRESET_TOPIC`（executor 订阅用）

### 2.2 传统控制 topic（真正控制机器人）

机器人真正控制通常使用：

- `/cmd_vel` `geometry_msgs/msg/Twist`
- `/cmd_vel_stamped` `geometry_msgs/msg/TwistStamped`

项目默认 mapping 已包含这两种。

---

## 3. 配置文件

### 3.1 motion presets allowlist（控制白名单）

文件：`python/config/motion_control_presets.yaml`

示例：

```yaml
presets:
  stop:
    description: "Stop safely"
  forward:
    description: "Forward using /cmd_vel Twist"
  forward_stamped:
    description: "Forward using /cmd_vel_stamped TwistStamped"
```

控制 MCP server 只允许执行此文件中存在的 preset。

### 3.2 preset → 控制 topic 映射（执行器映射）

文件：`python/config/preset_topic_mapping.yaml`

格式：

```yaml
presets:
  <preset_name>:
    publish:
      - topic: <topic_name>
        type: <pkg>/msg/<MsgName>
        msg: <dict>
```

#### 时间戳宏（TwistStamped 等）

若 message 字段包含 ROS2 Time（例如 `header.stamp`），可以写：

```yaml
header:
  stamp: {__now__: true}
```

执行器会把它替换为当前 ROS2 clock 时间。

---

## 4. 运行（Mock 模式快速验证）

### 4.1 启动观测 MCP servers（mock）

```bash
./scripts/start-observation-mock.sh
```

默认端口（由 `python/config/servers.yaml` 决定）：
- core SSE：`http://127.0.0.1:8080/sse`
- joints SSE：`http://127.0.0.1:8081/sse`

### 4.2 启动控制面（motion-control + executor + UI）

```bash
./scripts/start-control.sh
```

默认：
- motion-control SSE：`http://127.0.0.1:8087/sse`
- UI：`http://127.0.0.1:7861`

> 注意：executor 需要 ROS2 环境（rclpy + message packages）。

### 4.3 启动 Loop runtime（Node）

```bash
cd runtime
cp loop.example.yaml loop.yaml

# 设置你的 LLM API Key（取决于 provider）
# export GOOGLE_API_KEY=...

npm run dev -- --config ./loop.yaml
```

runtime 会写 JSONL 日志（默认 `./mcp_loop_actions.jsonl`），可在 UI 的 “Loop Report” 查看。

---

## 5. 运行（真机）

- 将 `MANASTONE_MOCK_MODE=false`
- 确保 ROS2 环境可访问 DDS（如需）
- 在 `python/config/servers.yaml` 中启用你需要的观测 server
- 建议先将 runtime 的 `dryRun: true` 保持开启，确认观测与决策链路稳定后再关闭。
