#!/usr/bin/env python3
"""
任务工作区管理 - 参考 agent-council

为每个任务创建独立空间：
tasks/<task_id>/
├── SOUL.md (任务定义)
├── HEARTBEAT.md (状态持久化)
└── result.md (最终结果)
"""

import json
import os
from datetime import datetime
from pathlib import Path

WORKSPACE = Path("/home/admin/.openclaw/workspace")
TASKS_DIR = WORKSPACE / "tasks"


class TaskWorkspaceManager:
    """任务工作区管理器"""
    
    @staticmethod
    def setup_workspace(task_id: str, task_name: str, task_description: str = "") -> Path:
        """
        创建任务工作区
        
        参数：
            task_id: 任务 ID（唯一标识）
            task_name: 任务名称
            task_description: 任务描述
        
        返回：
            工作区路径
        """
        workspace_path = TASKS_DIR / task_id
        workspace_path.mkdir(parents=True, exist_ok=True)
        
        # 创建 SOUL.md (任务定义)
        soul_md = workspace_path / "SOUL.md"
        soul_md.write_text(f"""# SOUL.md - {task_name}

**任务 ID:** {task_id}
**创建时间:** {datetime.now().isoformat()}

## 任务描述
{task_description}

## 职责
- 完成 {task_name}
- 遵循依赖关系
- 每步验证结果

## 边界
- 不修改系统文件
- 不删除用户数据
- 单步耗时 <8 分钟

## 状态
- 当前步骤：0
- 已完成：[]
- 失败：[]
""", encoding='utf-8')
        
        # 创建 HEARTBEAT.md (初始状态)
        heartbeat_md = workspace_path / "HEARTBEAT.md"
        heartbeat_md.write_text(json.dumps({
            "task_id": task_id,
            "task_name": task_name,
            "created_at": datetime.now().isoformat(),
            "last_heartbeat": datetime.now().isoformat(),
            "current_step": 0,
            "status": "initialized",
            "steps_completed": [],
            "steps_failed": []
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        
        print(f"✅ 任务工作区已创建：{workspace_path}")
        return workspace_path
    
    @staticmethod
    def get_workspace_path(task_id: str) -> Path:
        """获取任务工作区路径"""
        return TASKS_DIR / task_id
    
    @staticmethod
    def workspace_exists(task_id: str) -> bool:
        """检查工作区是否存在"""
        return (TASKS_DIR / task_id).exists()
    
    @staticmethod
    def load_soul(task_id: str) -> dict:
        """加载任务定义 (SOUL.md)"""
        soul_path = TASKS_DIR / task_id / "SOUL.md"
        if not soul_path.exists():
            return {}
        
        content = soul_path.read_text(encoding='utf-8')
        # 简单解析 Markdown（实际可以用更复杂的解析）
        return {
            "task_id": task_id,
            "content": content
        }
    
    @staticmethod
    def load_heartbeat(task_id: str) -> dict:
        """加载心跳状态 (HEARTBEAT.md)"""
        heartbeat_path = TASKS_DIR / task_id / "HEARTBEAT.md"
        if not heartbeat_path.exists():
            return {}
        
        content = heartbeat_path.read_text(encoding='utf-8')
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {}
    
    @staticmethod
    def save_heartbeat(task_id: str, state: dict):
        """保存心跳状态"""
        heartbeat_path = TASKS_DIR / task_id / "HEARTBEAT.md"
        state["last_heartbeat"] = datetime.now().isoformat()
        heartbeat_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    
    @staticmethod
    def save_result(task_id: str, result: str):
        """保存最终结果"""
        result_path = TASKS_DIR / task_id / "result.md"
        result_path.write_text(f"""# 任务结果 - {task_id}

**完成时间:** {datetime.now().isoformat()}

---

{result}
""", encoding='utf-8')
        print(f"✅ 结果已保存：{result_path}")
    
    @staticmethod
    def cleanup_workspace(task_id: str):
        """清理任务工作区"""
        workspace_path = TASKS_DIR / task_id
        if workspace_path.exists():
            import shutil
            shutil.rmtree(workspace_path)
            print(f"🗑️ 工作区已清理：{workspace_path}")
    
    @staticmethod
    def gc_old_tasks(max_age_days: int = 7):
        """清理过期任务"""
        if not TASKS_DIR.exists():
            return
        
        cutoff = datetime.now().timestamp() - (max_age_days * 24 * 60 * 60)
        cleaned = 0
        
        for task_dir in TASKS_DIR.iterdir():
            if not task_dir.is_dir():
                continue
            
            heartbeat_path = task_dir / "HEARTBEAT.md"
            if not heartbeat_path.exists():
                continue
            
            try:
                content = heartbeat_path.read_text(encoding='utf-8')
                data = json.loads(content)
                created_at = datetime.fromisoformat(data.get("created_at", "")).timestamp()
                
                if created_at < cutoff:
                    import shutil
                    shutil.rmtree(task_dir)
                    cleaned += 1
                    print(f"🗑️ 清理过期任务：{task_dir.name}")
            except Exception as e:
                print(f"⚠️ 清理失败 {task_dir.name}: {e}")
        
        if cleaned > 0:
            print(f"✅ 共清理 {cleaned} 个过期任务")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法：python task_workspace.py <task_id> [task_name]")
        sys.exit(1)
    
    task_id = sys.argv[1]
    task_name = sys.argv[2] if len(sys.argv) > 2 else "测试任务"
    
    workspace = TaskWorkspaceManager.setup_workspace(task_id, task_name)
    print(f"工作区路径：{workspace}")
