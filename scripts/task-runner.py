#!/usr/bin/env python3
"""
自动任务执行器 v3.0 - 健康长任务执行系统 (Health-First Protocol)

核心特性：
- 双模型协作 (qwen3.5-plus + claude-sonnet-4.6)
- 10 分钟心跳切片 + 状态落盘 + GC
- 依赖管理 + 动态纠偏
- 检查点持久化 + 崩溃恢复

架构：
┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ Architect   │→ │ Inspector   │→ │ Executor    │→ │ Tester      │
│ (双模型商量) │  │ (依赖检查)  │  │ (qwen 执行)  │  │ (双模型验收)│
└─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘
                        ↑                ↓
                        └── Verifier ←───┘
                           (claude 验证)
"""

import json
import os
import sys
import signal
import gc
import re
from datetime import datetime
from pathlib import Path

# 配置
WORKSPACE = Path("/home/admin/.openclaw/workspace")
CHECKPOINT_DIR = WORKSPACE / ".checkpoints"
PLANS_DIR = WORKSPACE / "plans"
REPORTS_DIR = WORKSPACE / "reports"
LOGS_DIR = WORKSPACE / "logs"
TODOS_FILE = WORKSPACE / "TODOS.md"
STATE_SNAPSHOT_FILE = WORKSPACE / ".state_snapshot.json"

# 确保目录存在
for d in [CHECKPOINT_DIR, PLANS_DIR, REPORTS_DIR, LOGS_DIR]:
    d.mkdir(exist_ok=True, parents=True)

# 导入模块
sys.path.insert(0, str(Path(__file__).parent))
try:
    from architect import generate_plan
    from executor import execute_step_local
    from verifier import verify_step, load_execution_result
    from tester import generate_report
    from model_client import call_model, MODEL_QWEN, MODEL_CLAUDE
    from task_workspace import WorkspaceManager, TaskWorkspace
except ImportError as e:
    print(f"⚠️ 模块导入失败：{e}")
    print("使用简化模式（本地执行 + 本地验证）")
    
    # 简化版本的导入（当双模型功能不可用时）
    def generate_plan(task_name, session_key=None):
        return {"task_name": task_name, "steps": [], "status": "simplified"}
    
    def verify_step(step, exec_result, session_key=None):
        return {"verifier_decision": "done" if exec_result.get("success") else "failed"}
    
    def generate_report(task_name, session_key=None):
        return {"status": "simplified"}
    
    def call_model(prompt, model, label="专家", timeout_seconds=300):
        return f"[模拟响应：{label}]"
    
    MODEL_QWEN = "dashscope-coding/qwen3.5-plus"
    MODEL_CLAUDE = "openai/claude-sonnet-4-6"
    
    class WorkspaceManager:
        def create_workspace(self, task_name, session_key=None, force=False):
            return None
        def get_workspace(self, task_name, session_key=None):
            return None


class HeartbeatManager:
    """心跳管理器 - 10 分钟强制切片"""
    
    def __init__(self, interval_minutes=10):
        self.interval = interval_minutes * 60
        self.last_heartbeat = datetime.now()
        self.state_file = STATE_SNAPSHOT_FILE
    
    def check_heartbeat(self) -> bool:
        """检查是否需要触发心跳"""
        elapsed = (datetime.now() - self.last_heartbeat).total_seconds()
        return elapsed >= self.interval
    
    def trigger_heartbeat(self, state: dict):
        """触发心跳：保存状态 + GC"""
        log("💓 心跳触发 - 开始状态落盘")
        
        # 1. 状态序列化
        self._save_state(state)
        
        # 2. 强制 GC
        gc.collect()
        
        # 3. 内存检查
        mem = self._check_memory()
        if mem.get('available_mb', 1000) < 300:
            log("⚠️ 低内存警告 - 触发额外清理")
            self._aggressive_cleanup()
        
        # 4. 重置计时器
        self.last_heartbeat = datetime.now()
        log(f"✅ 心跳完成 - 可用内存 {mem.get('available_mb', '?')}MB")
    
    def _save_state(self, state: dict):
        """保存状态到磁盘"""
        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "state": state
        }
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    
    def _check_memory(self) -> dict:
        """检查内存状态"""
        try:
            with open('/proc/meminfo') as f:
                meminfo = {}
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        key = parts[0].rstrip(':')
                        value = int(parts[1]) / 1024  # KB -> MB
                        meminfo[key] = value
                
                return {
                    'total_mb': meminfo.get('MemTotal', 0),
                    'available_mb': meminfo.get('MemAvailable', 0),
                    'free_mb': meminfo.get('MemFree', 0)
                }
        except Exception as e:
            return {'error': str(e)}
    
    def _aggressive_cleanup(self):
        """激进清理"""
        # 清空 Python 缓存
        gc.collect()
        # 可以添加更多清理逻辑


class TaskOrchestrator:
    """任务编排器 - 状态机实现"""
    
    def __init__(self, session_key: str = None):
        self.session_key = session_key or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.role = "architect"  # 当前角色
        self.state = {
            "task_name": None,
            "plan": None,
            "current_step": 0,
            "completed_steps": [],
            "failed_steps": [],
            "status": "idle",
            "workspace_id": None
        }
        self.heartbeat = HeartbeatManager(interval_minutes=8)  # 8 分钟心跳，留缓冲
        self.workspace_manager = WorkspaceManager()
        self.workspace: TaskWorkspace = None
    
    def run_task(self, task_name: str, force_restart: bool = False):
        """运行任务"""
        log(f"🚀 开始任务：{task_name}")
        self.state["task_name"] = task_name
        self.state["status"] = "running"
        
        # 清理过期工作区
        self.workspace_manager.cleanup_old_workspaces(max_age_days=7)
        
        # 检查是否有已保存的状态（崩溃恢复）
        if not force_restart and STATE_SNAPSHOT_FILE.exists():
            self._load_state()
            if self.state.get("task_name") == task_name:
                log(f"📌 恢复之前的状态：步骤 {self.state['current_step']}")
                # 恢复工作区
                if self.state.get("workspace_id"):
                    self.workspace = self.workspace_manager.get_workspace(
                        task_name, 
                        self.session_key
                    )
        
        # 创建工作区（如果不存在）
        if self.workspace is None:
            log("📂 创建任务工作区")
            self.workspace = self.workspace_manager.create_workspace(
                task_name, 
                self.session_key,
                force=force_restart
            )
            if self.workspace:
                self.state["workspace_id"] = self.workspace.workspace_id
        
        # 阶段 1: 建筑师 - 生成计划
        if self.state.get("plan") is None:
            log("📐 阶段 1: 建筑师 - 生成计划")
            self.state["plan"] = self._run_architect(task_name)
            self._save_state()
        
        # 阶段 2: 执行循环
        log("🏃 阶段 2: 执行循环")
        self._run_execution_loop()
        
        # 阶段 3: 测试师 - 生成报告
        log("✅ 阶段 3: 测试师 - 生成报告")
        self._run_tester(task_name)
        
        log(f"🎉 任务完成：{task_name}")
        self.state["status"] = "completed"
        self._save_state()
        
        # 任务完成后归档工作区
        if self.workspace and not force_restart:
            log("📦 归档任务工作区")
            self.workspace.destroy()
    
    def _run_architect(self, task_name: str) -> dict:
        """建筑师阶段 - 双模型商量生成计划"""
        # 使用 model_client 调用双模型
        architect_prompt = f"""
你是 Architect（建筑师），负责为任务制定详细的执行计划。

任务名称：{task_name}

请生成一个步骤列表，每个步骤包含：
1. id: 步骤编号（从 1 开始）
2. name: 步骤名称（简洁描述）
3. cmd: 要执行的命令
4. dependencies: 依赖的步骤 ID 列表（空列表表示无依赖）
5. estimated_minutes: 预计耗时（分钟）

要求：
- 每个步骤预计耗时 <8 分钟
- 依赖关系必须合理（不能循环依赖）
- 命令必须是可执行的 shell 命令

请以 JSON 格式输出步骤列表，例如：
[
  {{"id": 1, "name": "分析现有结构", "cmd": "ls -lh", "dependencies": [], "estimated_minutes": 2}},
  {{"id": 2, "name": "编写脚本", "cmd": "echo 'script'", "dependencies": [1], "estimated_minutes": 5}}
]
"""
        
        try:
            # 调用 qwen 生成计划
            log("🧠 调用 qwen 生成计划...")
            qwen_response = call_model(architect_prompt, MODEL_QWEN, "Architect-Qwen", timeout_seconds=180)
            
            # 尝试解析 JSON
            plan_steps = self._parse_plan_from_response(qwen_response)
            
            if plan_steps:
                log(f"✅ 计划生成成功：{len(plan_steps)} 个步骤")
                return {
                    "task_name": task_name,
                    "steps": plan_steps,
                    "status": "approved",
                    "generated_by": "qwen3.5-plus"
                }
            else:
                log("⚠️ 解析失败，使用默认计划")
                return {
                    "task_name": task_name,
                    "steps": self._generate_default_steps(task_name),
                    "status": "approved",
                    "generated_by": "fallback"
                }
        except Exception as e:
            log(f"⚠️ 建筑师调用失败：{e}")
            return {
                "task_name": task_name,
                "steps": self._generate_default_steps(task_name),
                "status": "approved",
                "generated_by": "error_fallback"
            }
    
    def _parse_plan_from_response(self, response: str) -> list:
        """从模型响应中解析计划"""
        import re
        try:
            # 尝试提取 JSON 数组
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                plan_json = json_match.group()
                return json.loads(plan_json)
        except Exception as e:
            log(f"⚠️ JSON 解析失败：{e}")
        return []
    
    def _generate_default_steps(self, task_name: str) -> list:
        """生成默认步骤（简化版，实际应该由双模型商量）"""
        # 从 TODOS.md 解析任务，生成默认步骤
        default_steps = {
            "日志轮转脚本": [
                {"id": 1, "name": "分析现有日志结构", "cmd": "ls -lh /home/admin/.openclaw/workspace/logs/", "dependencies": [], "estimated_minutes": 2},
                {"id": 2, "name": "编写轮转脚本", "cmd": "echo '脚本编写中...'", "dependencies": [1], "estimated_minutes": 5},
                {"id": 3, "name": "测试脚本", "cmd": "bash /home/admin/.openclaw/workspace/scripts/rotate-logs.sh", "dependencies": [2], "estimated_minutes": 3},
                {"id": 4, "name": "配置 cron", "cmd": "crontab -l", "dependencies": [3], "estimated_minutes": 2},
                {"id": 5, "name": "验证运行", "cmd": "crontab -l", "dependencies": [4], "estimated_minutes": 1}
            ],
            "记忆文件压缩归档": [
                {"id": 1, "name": "扫描记忆文件", "cmd": "ls -lh /home/admin/.openclaw/workspace/memory/*.md 2>/dev/null | head -20", "dependencies": [], "estimated_minutes": 2},
                {"id": 2, "name": "识别 30 天 + 文件", "cmd": "find /home/admin/.openclaw/workspace/memory -name '*.md' -mtime +30 -type f", "dependencies": [1], "estimated_minutes": 3},
                {"id": 3, "name": "创建备份目录", "cmd": "mkdir -p /home/admin/.openclaw/workspace/memory/backup", "dependencies": [2], "estimated_minutes": 1},
                {"id": 4, "name": "压缩旧文件", "cmd": "cd /home/admin/.openclaw/workspace/memory && tar -czvf backup/old-memories.tar.gz *.md 2>/dev/null || echo '无文件可压缩'", "dependencies": [3], "estimated_minutes": 5},
                {"id": 5, "name": "验证压缩结果", "cmd": "ls -lh /home/admin/.openclaw/workspace/memory/backup/", "dependencies": [4], "estimated_minutes": 1}
            ]
        }
        return default_steps.get(task_name, [
            {"id": 1, "name": "执行任务", "cmd": "echo '任务执行中...'", "dependencies": [], "estimated_minutes": 5}
        ])
    
    def _run_execution_loop(self):
        """执行循环 - 按步骤执行 + 验证"""
        plan = self.state.get("plan", {})
        steps = plan.get("steps", [])
        
        while self.state["current_step"] < len(steps):
            # 检查心跳
            if self.heartbeat.check_heartbeat():
                self.heartbeat.trigger_heartbeat(self.state)
            
            step = steps[self.state["current_step"]]
            
            # 检查依赖
            if not self._check_dependencies(step):
                log(f"🛑 步骤 {step['id']} 依赖未完成，跳过")
                self.state["current_step"] += 1
                continue
            
            log(f"▶️ 执行步骤 {step['id']}: {step['name']}")
            
            # 准备工作区上下文
            workspace_context = ""
            if self.workspace:
                workspace_context = f"\n工作区路径：{self.workspace.workspace_path}\n"
            
            # 执行（使用 executor 模块）
            exec_result = execute_step_local(step, workspace=self.workspace)
            
            # 保存执行结果到工作区
            if self.workspace:
                self.workspace.save_file(
                    f"step_{step['id']}_result.json",
                    json.dumps(exec_result, ensure_ascii=False, indent=2),
                    category="output"
                )
            
            # 验证（使用 claude 验证）
            verdict = self._verify_with_claude(step, exec_result)
            
            if verdict == "done":
                log(f"✅ 步骤 {step['id']} 完成")
                self.state["completed_steps"].append(step["id"])
            else:
                log(f"❌ 步骤 {step['id']} 失败：{verdict}")
                self.state["failed_steps"].append(step["id"])
                # 保存失败原因
                if self.workspace:
                    self.workspace.save_file(
                        f"step_{step['id']}_failure.txt",
                        f"失败原因：{verdict}",
                        category="output"
                    )
            
            self.state["current_step"] += 1
            self._save_state()
    
    def _check_dependencies(self, step: dict) -> bool:
        """检查依赖是否完成"""
        deps = step.get("dependencies", [])
        for dep in deps:
            if dep not in self.state["completed_steps"]:
                return False
        return True
    
    def _verify_with_claude(self, step: dict, exec_result: dict) -> str:
        """使用 claude 验证执行结果"""
        verify_prompt = f"""
你是 Verifier（验证器），负责验证步骤执行结果是否正确。

步骤信息：
- ID: {step['id']}
- 名称：{step['name']}
- 命令：{step.get('cmd', 'N/A')}

执行结果：
- 成功：{exec_result.get('success', False)}
- 输出：{exec_result.get('output', 'N/A')[:500]}
- 错误：{exec_result.get('error', 'N/A')[:200] if exec_result.get('error') else '无'}

请判断该步骤是否完成：
- 如果完成，回复："done"
- 如果失败，回复："failed: 原因"

简短回复即可。
"""
        
        try:
            log("🧠 调用 claude 验证...")
            claude_response = call_model(verify_prompt, MODEL_CLAUDE, "Verifier-Claude", timeout_seconds=120)
            
            response_lower = claude_response.strip().lower()
            if response_lower.startswith("done"):
                return "done"
            elif response_lower.startswith("failed"):
                # 提取失败原因
                reason = claude_response.split(":", 1)[-1].strip() if ":" in claude_response else "未知原因"
                return reason
            else:
                # 默认判断
                if exec_result.get("success", False):
                    return "done"
                return "未知验证结果"
        except Exception as e:
            log(f"⚠️ 验证调用失败：{e}")
            # 降级到本地验证
            return "done" if exec_result.get("success", False) else "failed"
    
    def _local_verify(self, step: dict, exec_result: dict) -> str:
        """本地验证（简化版，备用）"""
        if exec_result.get("success", False):
            return "done"
        return "failed"
    
    def _run_tester(self, task_name: str):
        """测试师阶段 - 双模型商量生成报告"""
        # 准备工作区上下文
        workspace_context = {}
        if self.workspace:
            workspace_context = self.workspace.get_context()
        
        # 生成报告
        report_prompt = f"""
你是 Tester（测试师），负责为任务生成完成报告。

任务名称：{task_name}
工作区 ID: {workspace_context.get('workspace_id', 'N/A')}

执行情况：
- 总步骤数：{len(self.state.get('plan', {}).get('steps', []))}
- 完成步骤：{len(self.state.get('completed_steps', []))}
- 失败步骤：{len(self.state.get('failed_steps', []))}

完成步骤 ID: {self.state.get('completed_steps', [])}
失败步骤 ID: {self.state.get('failed_steps', [])}

请生成一个简洁的报告，包含：
1. 任务整体状态（成功/部分完成/失败）
2. 关键成果
3. 遇到的问题（如有）
4. 后续建议（如有）

以 Markdown 格式输出。
"""
        
        try:
            log("🧠 生成测试报告...")
            report_content = call_model(report_prompt, MODEL_QWEN, "Tester-Qwen", timeout_seconds=180)
            
            # 保存报告到工作区
            if self.workspace:
                self.workspace.save_file("Report.md", report_content, category="output")
            
            # 同时保存到 reports 目录
            report_file = REPORTS_DIR / f"{task_name}_{self.session_key}.md"
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write(f"# 任务报告：{task_name}\n\n")
                f.write(f"生成时间：{datetime.now().isoformat()}\n\n")
                f.write(report_content)
            
            log(f"📊 报告已保存：{report_file}")
        except Exception as e:
            log(f"⚠️ 报告生成失败：{e}")
    
    def _save_state(self):
        """保存状态"""
        self.heartbeat._save_state(self.state)
    
    def _load_state(self):
        """加载状态"""
        if STATE_SNAPSHOT_FILE.exists():
            with open(STATE_SNAPSHOT_FILE, 'r', encoding='utf-8') as f:
                snapshot = json.load(f)
                self.state = snapshot.get("state", self.state)


def log(message: str):
    """记录日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {message}\n"
    log_file = LOGS_DIR / "task-runner.log"
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(log_line)
    print(log_line.strip())


def list_tasks():
    """列出任务状态"""
    log("=== 任务状态 ===")
    for cp_file in sorted(CHECKPOINT_DIR.glob("step_*_verified.json")):
        with open(cp_file, 'r', encoding='utf-8') as f:
            cp = json.load(f)
        log(f"步骤 {cp['step_id']}: {cp.get('step_name', 'N/A')} - {cp.get('verifier_decision', '?')}")
    
    if STATE_SNAPSHOT_FILE.exists():
        with open(STATE_SNAPSHOT_FILE, 'r', encoding='utf-8') as f:
            snapshot = json.load(f)
        state = snapshot.get("state", {})
        log(f"当前任务：{state.get('task_name', 'N/A')}")
        log(f"进度：{len(state.get('completed_steps', []))}/{state.get('current_step', 0)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python task-runner.py <命令> [任务名]")
        print("命令：run, list, restart")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "run":
        if len(sys.argv) < 3:
            print("用法：python task-runner.py run <任务名>")
            sys.exit(1)
        task_name = sys.argv[2]
        orchestrator = TaskOrchestrator()
        orchestrator.run_task(task_name)
    
    elif command == "list":
        list_tasks()
    
    elif command == "restart":
        if len(sys.argv) < 3:
            print("用法：python task-runner.py restart <任务名>")
            sys.exit(1)
        task_name = sys.argv[2]
        orchestrator = TaskOrchestrator()
        orchestrator.run_task(task_name, force_restart=True)
    
    else:
        print(f"未知命令：{command}")
        sys.exit(1)
