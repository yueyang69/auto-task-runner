#!/usr/bin/env python3
"""
验证器模块 - claude-sonnet-4.6 审查执行结果

参考：dual-expert-chat 降级策略
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path

WORKSPACE = Path("/home/admin/.openclaw/workspace")
CHECKPOINT_DIR = WORKSPACE / ".checkpoints"
CHECKPOINT_DIR.mkdir(exist_ok=True)

# 模型配置
MODEL_CLAUDE = "openai/claude-sonnet-4-6"


def call_model(prompt: str, model: str, label: str = "验证器") -> str:
    """调用模型 - 支持降级策略"""
    try:
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


def verify_step(step: dict, execution_result: dict, session_key: str = None) -> dict:
    """
    验证步骤执行结果
    
    参数：
        step: 步骤定义
        execution_result: 执行器输出
        session_key: 会话 key（可选，用于兼容）
    
    返回：
        {
            "step_id": 1,
            "verifier": "openai/claude-sonnet-4-6",
            "verifier_decision": "done" | "failed" | "retry",
            "verifier_reason": "...",
            "verified_at": "..."
        }
    """
    result = {
        "step_id": step["id"],
        "step_name": step["name"],
        "verifier": MODEL_CLAUDE,
        "verified_at": datetime.now().isoformat(),
        "verifier_decision": "failed",
        "verifier_reason": ""
    }
    
    print(f"🔍 验证器启动：步骤 {step['id']} - {step['name']}")
    
    # 使用 claude-sonnet-4.6 验证
    response = call_model(
        f"""验证步骤执行结果：

**步骤 {step['id']}:** {step['name']}

**执行命令:**
```bash
{step.get('cmd', 'N/A')}
```

**执行输出:**
```
{execution_result.get('executor_output', '无输出')}
```

请判断：
1. 执行是否成功？
2. 输出是否符合预期？
3. 是否需要重试？

回复格式：
```json
{{
  "decision": "done" | "failed" | "retry",
  "reason": "判断理由"
}}
```""",
        MODEL_CLAUDE,
        "验证器 (Claude)"
    )
    
    # 解析验证结果
    verdict = parse_verdict(response)
    result["verifier_decision"] = verdict.get("decision", "failed")
    result["verifier_reason"] = verdict.get("reason", "无法解析验证结果")
    
    # 保存验证结果
    save_verification_result(result)
    
    return result


def parse_verdict(response: str) -> dict:
    """从模型响应中解析验证结论"""
    import re
    
    # 尝试提取 JSON
    json_match = re.search(r'```json\s*(.+?)\s*```', response, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass
    
    # 降级：根据关键词判断
    if "done" in response.lower() and "success" in response.lower():
        return {"decision": "done", "reason": "执行成功"}
    elif "retry" in response.lower():
        return {"decision": "retry", "reason": "需要重试"}
    else:
        return {"decision": "failed", "reason": "执行失败"}


def save_verification_result(result: dict):
    """保存验证结果到检查点"""
    checkpoint_file = CHECKPOINT_DIR / f"step_{result['step_id']}_verified.json"
    with open(checkpoint_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def load_execution_result(step_id: int) -> dict:
    """加载执行结果"""
    checkpoint_file = CHECKPOINT_DIR / f"step_{step_id}_executed.json"
    if checkpoint_file.exists():
        with open(checkpoint_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法：python verifier.py <step_id>")
        sys.exit(1)
    
    step_id = int(sys.argv[1])
    exec_result = load_execution_result(step_id)
    if exec_result:
        print(f"步骤 {step_id} 执行结果：{exec_result}")
    else:
        print(f"未找到步骤 {step_id} 的执行结果")
