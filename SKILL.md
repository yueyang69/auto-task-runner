---
name: auto-task-runner
description: 自动任务执行器 v3.0 - 双模型协作 + 心跳持久化 + 依赖管理
author: OpenClaw
version: 3.0.0
triggers:
  - "执行任务"
  - "自动任务"
  - "任务进度"
metadata:
  requires:
    bins: ["python3", "crontab"]
  config:
    env:
      WORKSPACE:
        description: "OpenClaw 工作区路径"
        default: "/home/admin/.openclaw/workspace"
---

# 自动任务执行器 v3.0

**健康长任务执行系统 (Health-First Protocol)**

---

## 🎯 核心特性

| 特性 | 说明 |
|------|------|
| **双模型协作** | qwen3.5-plus (执行) + claude-sonnet-4.6 (验证) |
| **10 分钟心跳** | 强制状态落盘 + GC + 内存检查 |
| **依赖管理** | 步骤依赖标注 + 动态纠偏 |
| **检查点持久化** | 每步保存，崩溃可恢复 |
| **2GB 内存优化** | 状态机架构，内存友好 |

---

## 🏗️ 架构

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ Architect   │→ │ Inspector   │→ │ Executor    │→ │ Tester      │
│ (双模型商量) │  │ (依赖检查)  │  │ (qwen 执行)  │  │ (双模型验收)│
└─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘
                        ↑                ↓
                        └── Verifier ←───┘
                           (claude 验证)
```

### 四阶段流程

1. **📐 建筑师 (Architects)** - qwen ←→ claude 商量生成 PlanList.md
2. **🏃 执行循环** - 每个步骤：qwen 执行 → claude 验证 → 写检查点
3. **✅ 测试师 (Tester)** - qwen ←→ claude 商量生成 Report.md
4. **💓 心跳** - 每 8-10 分钟强制保存状态 + GC

---

## 📝 命令

### 执行任务
```bash
cd /home/admin/.openclaw/workspace/skills/auto-task-runner
python3 scripts/task-runner.py run "任务名称"
```

### 查看任务状态
```bash
python3 scripts/task-runner.py list
```

### 重启任务（从头开始）
```bash
python3 scripts/task-runner.py restart "任务名称"
```

---

## 📁 文件结构

```
auto-task-runner/
├── scripts/
│   ├── task-runner.py          # 主入口 (orchestrator)
│   ├── architect.py            # 建筑师模块 (双模型商量计划)
│   ├── executor.py             # 执行器模块 (qwen 执行)
│   ├── verifier.py             # 验证器模块 (claude 验证)
│   └── tester.py               # 测试师模块 (双模型验收)
├── plans/                      # 建筑师输出的计划
│   └── PlanList.md
├── reports/                    # 测试师输出的报告
│   └── Report.md
├── .checkpoints/               # 检查点目录
│   ├── step_1_executed.json
│   ├── step_1_verified.json
│   └── ...
├── .state_snapshot.json        # 状态快照 (心跳保存)
└── SKILL.md
```

---

## 📋 PlanList.md 格式

```markdown
# PlanList.md - 日志轮转脚本

| Step ID | 步骤名称 | 依赖 | 预计耗时 | 命令 | 状态 |
|---------|---------|------|---------|------|------|
| 1 | 分析现有日志结构 | 无 | 2min | `ls -lh logs/` | done |
| 2 | 编写轮转脚本 | 1 | 5min | `cat > script.sh...` | done |
| 3 | 测试脚本 | 2 | 3min | `bash script.sh` | pending |
| 4 | 配置 cron | 3 | 2min | `crontab -e` | pending |
| 5 | 验证运行 | 4 | 1min | `crontab -l` | pending |
```

---

## 🔍 检查点格式

### 执行检查点 (step_N_executed.json)
```json
{
  "step_id": 1,
  "step_name": "分析现有日志结构",
  "executor": "dashscope-coding/qwen3.5-plus",
  "executor_output": "logs/ 目录下有 3 个文件...",
  "success": true,
  "executed_at": "2026-03-14T20:00:00"
}
```

### 验证检查点 (step_N_verified.json)
```json
{
  "step_id": 1,
  "step_name": "分析现有日志结构",
  "verifier": "openai/claude-sonnet-4-6",
  "verifier_decision": "done",
  "verifier_reason": "输出包含完整的日志文件列表，分析完成",
  "verified_at": "2026-03-14T20:02:00"
}
```

---

## ⏱️ 心跳机制

### 时间分配（8-10 分钟）

| 操作 | 模型 | 耗时 |
|------|------|------|
| qwen 执行命令 | qwen3.5-plus | 3-4min |
| 切换模型 | - | 30s-1min |
| claude 验证 | claude-sonnet-4.6 | 2-3min |
| 保存检查点 + GC | - | 1min |
| **总计** | - | **7-9min** ✅ |

### 心跳触发时

1. **状态序列化** → `.state_snapshot.json`
2. **强制 GC** → 释放内存
3. **内存检查** → <300MB 触发额外清理
4. **重置计时器** → 继续执行

---

## 🛠️ 添加新任务

在 `task-runner.py` 的 `_generate_default_steps` 方法中添加：

```python
"新任务名称": [
    {"id": 1, "name": "步骤 1", "cmd": "命令 1", "dependencies": [], "estimated_minutes": 3},
    {"id": 2, "name": "步骤 2", "cmd": "命令 2", "dependencies": [1], "estimated_minutes": 5},
    ...
]
```

---

## 📊 日志

位置：`/home/admin/.openclaw/workspace/logs/task-runner.log`

查看日志：
```bash
tail -f /home/admin/.openclaw/workspace/logs/task-runner.log
```

---

## ⚠️ 注意事项

1. **任务命名必须精确匹配** - "日志轮转脚本" 不能写成 "日志轮转"
2. **每步预计耗时 <8 分钟** - 适配心跳机制
3. **依赖关系必须正确** - 否则会被 Inspector 拦截
4. **检查点持久化** - 崩溃后自动恢复

---

## 🔄 崩溃恢复

任务执行中如果崩溃（内存溢出、网络中断等）：

1. 重新运行 `python3 task-runner.py run "任务名称"`
2. 自动加载 `.state_snapshot.json`
3. 从最后完成的步骤继续

---

*版本：3.0.0 | 最后更新：2026-03-14*
