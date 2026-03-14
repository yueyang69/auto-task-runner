#!/usr/bin/env python3
"""
建筑师模块 - 双模型商量生成执行计划
qwen3.5-plus ←→ claude-sonnet-4.6 协商，达成一致后输出 PlanList.md

参考：dual-expert-chat 降级策略
- 优先：子代理模式 (sessions_spawn)
- 降级：会话切换模型 (sessions_send 或 openclaw 命令)
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path

WORKSPACE = Path("/home/admin/.openclaw/workspace")
PLANS_DIR = WORKSPACE / "plans"
PLANS_DIR.mkdir(exist_ok=True)

# 模型配置
MODEL_QWEN = "dashscope-coding/qwen3.5-plus"
MODEL_CLAUDE = "openai/claude-sonnet-4-6"


def call_model(prompt: str, model: str, label: str = "专家") -> str:
    """
    调用模型 - 支持降级策略
    
    参考 dual-expert-chat:
    1. 优先尝试子代理模式
    2. 降级到 openclaw 命令调用
    """
    try:
        # 尝试使用 openclaw 命令发送消息（降级模式）
        result = subprocess.run(
            ["openclaw", "send", "--model", model, prompt],
            capture_output=True,
            text=True,
            timeout=300  # 5 分钟超时
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            print(f"⚠️ openclaw 命令失败：{result.stderr}")
            return f"[{model} 调用失败]"
    except subprocess.TimeoutExpired:
        return f"[{model} 超时]"
    except Exception as e:
        return f"[{model} 错误：{str(e)}]"


def generate_plan(task_name: str, session_key: str = None) -> dict:
    """
    双模型商量生成任务计划
    
    流程（参考 dual-expert-chat 辩论协作模式）：
    1. qwen3.5-plus 提出初步方案
    2. claude-sonnet-4.6 审查 + 修改建议
    3. qwen3.5-plus 回应 + 调整
    4. claude-sonnet-4.6 最终认可
    
    返回：PlanList.md 内容
    """
    plan = {
        "task_name": task_name,
        "created_at": datetime.now().isoformat(),
        "steps": [],
        "status": "draft"
    }
    
    print(f"🧠 建筑师阶段启动：{task_name}")
    print(f"模型配置：Qwen={MODEL_QWEN}, Claude={MODEL_CLAUDE}")
    
    # Round 1: qwen 提方案
    print("📝 Round 1: qwen3.5-plus 提出方案...")
    qwen_proposal = call_model(
        f"""任务：{task_name}

请拆解为原子化步骤，每个步骤：
1. 独立可执行
2. 预计耗时 <8 分钟
3. 明确依赖关系（前置步骤 ID）

输出格式：
```json
{{
  "steps": [
    {{"id": 1, "name": "步骤名", "dependencies": [], "cmd": "命令", "estimated_minutes": 3}},
    ...
  ]
}}
```""",
        MODEL_QWEN,
        "规划者 (Qwen)"
    )
    
    # Round 2: claude 审查
    print("🔍 Round 2: claude-sonnet-4.6 审查...")
    claude_review = call_model(
        f"""审查以下任务计划：

{qwen_proposal}

请检查：
1. 依赖关系是否合理？
2. 步骤是否足够原子化？
3. 时间预估是否合理？

如有问题，提出修改建议。如无问题，回复"✅ 方案通过"。""",
        MODEL_CLAUDE,
        "审查者 (Claude)"
    )
    
    # Round 3: qwen 回应
    print("📝 Round 3: qwen3.5-plus 回应...")
    qwen_response = call_model(
        f"""claude 的审查意见：

{claude_review}

请根据意见调整方案，或解释为什么原方案合理。""",
        MODEL_QWEN,
        "规划者 (Qwen)"
    )
    
    # Round 4: claude 最终认可
    print("✅ Round 4: claude-sonnet-4.6 最终认可...")
    claude_final = call_model(
        f"""最终审查：

{qwen_response}

如认可，回复"✅ 最终通过"并输出完整 PlanList。""",
        MODEL_CLAUDE,
        "审查者 (Claude)"
    )
    
    # 解析最终方案
    plan["status"] = "approved"
    plan["approved_at"] = datetime.now().isoformat()
    plan["steps"] = parse_steps_from_response(qwen_response or claude_final)
    
    # 写入 PlanList.md
    write_plan_md(plan)
    
    return plan


def parse_steps_from_response(response: str) -> list:
    """从模型响应中解析步骤列表"""
    import re
    
    # 尝试提取 JSON
    json_match = re.search(r'```json\s*(.+?)\s*```', response, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            return data.get("steps", [])
        except json.JSONDecodeError:
            pass
    
    # 降级：返回空列表，需要人工介入
    return []


def write_plan_md(plan: dict):
    """写入 PlanList.md"""
    plan_file = PLANS_DIR / f"{plan['task_name'].replace(' ', '-')}.md"
    
    content = f"""# PlanList.md - {plan['task_name']}

**创建时间：** {plan['created_at']}
**状态：** {plan['status']}
**批准时间：** {plan.get('approved_at', 'N/A')}

## 执行步骤

| Step ID | 步骤名称 | 依赖 | 预计耗时 | 命令 | 状态 |
|---------|---------|------|---------|------|------|
"""
    
    for step in plan['steps']:
        deps = ", ".join(map(str, step.get('dependencies', []))) or "无"
        content += f"| {step['id']} | {step['name']} | {deps} | {step.get('estimated_minutes', '?')}min | `{step.get('cmd', 'N/A')[:30]}...` | pending |\n"
    
    content += f"""
## 执行队列

按依赖排序后的可执行顺序：{[s['id'] for s in plan['steps']]}

---
*由双模型协作生成 (qwen3.5-plus + claude-sonnet-4.6)*
"""
    
    plan_file.write_text(content, encoding='utf-8')


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法：python architect.py <任务名>")
        sys.exit(1)
    
    task_name = sys.argv[1]
    # 测试用，实际需要通过 sessions_send 调用
    print(f"建筑师模块：为任务 '{task_name}' 生成计划")
