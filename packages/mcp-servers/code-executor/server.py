"""MCP Code Executor Server - Python 代码沙箱执行

为 AI Town 角色提供"写代码 → 看结果"的工具能力。
角色可调用此 MCP Server 执行计算、数据处理、算法验证等任务。

设计：
- 使用 subprocess 隔离执行，避免主进程被污染
- 限制可执行时间（默认 30s）与代码长度（10KB）
- 通过环境变量与 globals 限制危险模块（基础防护）
- 生产环境建议升级为 Docker/nsjail 真沙箱

启动方式：
    uv run python server.py
    # 或 stdio 模式接入 MCP Client（Claude Desktop 等）
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import structlog
from fastmcp import FastMCP  # FastMCP 2.0+ 导入方式

logger = structlog.get_logger()

mcp = FastMCP("code-executor")

# 安全限制
MAX_CODE_LENGTH = 10 * 1024          # 10KB
MAX_TIMEOUT_SECONDS = 60             # 硬上限 60 秒
DEFAULT_TIMEOUT = 30                 # 默认 30 秒


# 执行模板：包裹用户代码，注入受限 globals
# 注意：此为基础防护，非真沙箱。生产环境需用 Docker/nsjail。
_EXEC_TEMPLATE = '''
import sys
import io
import contextlib

# ⚠️ 受限全局命名空间：移除危险模块
_RESTRICTED_NAMES = {
    "open", "exec", "eval", "compile", "__import__",
    "exit", "quit", "globals", "locals",
    "getattr", "setattr", "delattr",
}

_safe_builtins = {
    k: v for k, v in __builtins__.__dict__.items()
    if k not in _RESTRICTED_NAMES and not k.startswith("_")
}

# 允许使用的模块白名单
_allowed_modules = {}
try:
    import math
    _allowed_modules["math"] = math
except ImportError:
    pass
try:
    import json
    _allowed_modules["json"] = json
except ImportError:
    pass
try:
    import statistics
    _allowed_modules["statistics"] = statistics
except ImportError:
    pass
try:
    import datetime
    _allowed_modules["datetime"] = datetime
except ImportError:
    pass
try:
    import random
    _allowed_modules["random"] = random
except ImportError:
    pass
try:
    import re
    _allowed_modules["re"] = re
except ImportError:
    pass
try:
    import itertools
    _allowed_modules["itertools"] = itertools
except ImportError:
    pass
try:
    import collections
    _allowed_modules["collections"] = collections
except ImportError:
    pass


def _restricted_import(name, *args, **kwargs):
    if name in _allowed_modules:
        return _allowed_modules[name]
    raise ImportError(f"Module '{{name}}' is not allowed in sandbox")


_safe_builtins["__import__"] = _restricted_import

# 捕获 stdout / stderr
_stdout_buf = io.StringIO()
_stderr_buf = io.StringIO()

_user_ns = {{"__builtins__": _safe_builtins}}
_user_ns.update(_allowed_modules)

_user_code = """
{user_code}
"""

with contextlib.redirect_stdout(_stdout_buf), contextlib.redirect_stderr(_stderr_buf):
    try:
        exec(compile(_user_code, "<sandbox>", "exec"), _user_ns)
    except Exception as e:
        print(f"{{type(e).__name__}}: {{e}}", file=sys.stderr)

print("---STDOUT---")
print(_stdout_buf.getvalue(), end="")
print("---STDERR---")
print(_stderr_buf.getvalue(), end="")
'''


@mcp.tool()
async def execute_python(code: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """执行 Python 代码片段（沙箱环境）

    支持的模块白名单：math, json, statistics, datetime, random, re, itertools, collections
    禁止：open/exec/eval/__import__/subprocess 等危险操作

    Args:
        code: Python 代码片段（最多 10KB）
        timeout: 执行超时（秒），最大 60

    Returns:
        {
            "success": bool,
            "stdout": str,
            "stderr": str,
            "exit_code": int,
            "error": str | None,
        }
    """
    logger.info("execute_python_requested", code_length=len(code), timeout=timeout)

    # 输入校验
    if not code or not code.strip():
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "exit_code": -1,
            "error": "Empty code",
        }

    if len(code) > MAX_CODE_LENGTH:
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "exit_code": -1,
            "error": f"Code exceeds {MAX_CODE_LENGTH} bytes",
        }

    actual_timeout = min(max(timeout, 1), MAX_TIMEOUT_SECONDS)

    # 构造沙箱执行脚本
    sandbox_script = _EXEC_TEMPLATE.format(user_code=code)

    # 写入临时文件
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write(sandbox_script)
        script_path = Path(f.name)

    logger.debug("sandbox_script_written", path=str(script_path))

    try:
        # subprocess 隔离执行
        # - cwd 限制为临时目录
        # - env 仅保留 PYTHONPATH
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tempfile.gettempdir(),
            env={"PYTHONPATH": "", "PATH": "/usr/bin:/bin"},
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=actual_timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning(
                "execute_python_timeout",
                timeout=actual_timeout,
            )
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Execution timed out after {actual_timeout}s",
                "exit_code": -1,
                "error": "timeout",
            }

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # 解析沙箱模板的输出格式（---STDOUT--- / ---STDERR---）
        out_lines = stdout.split("---STDOUT---\n", 1)
        if len(out_lines) == 2:
            parsed_stdout = out_lines[1]
        else:
            parsed_stdout = stdout

        err_lines = parsed_stdout.split("---STDERR---\n", 1)
        if len(err_lines) == 2:
            parsed_stdout = err_lines[0]
            parsed_stderr = err_lines[1] + stderr
        else:
            parsed_stderr = stderr

        success = proc.returncode == 0 and not parsed_stderr.strip()

        logger.info(
            "execute_python_done",
            success=success,
            exit_code=proc.returncode,
            stdout_length=len(parsed_stdout),
            stderr_length=len(parsed_stderr),
        )

        return {
            "success": success,
            "stdout": parsed_stdout[:10_000],   # 截断 10KB
            "stderr": parsed_stderr[:10_000],
            "exit_code": proc.returncode,
            "error": None if success else "execution_failed",
        }

    except Exception as e:
        logger.error(
            "execute_python_error",
            error=str(e),
            exc_info=True,
        )
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "exit_code": -1,
            "error": type(e).__name__,
        }
    finally:
        # 清理临时文件
        try:
            script_path.unlink(missing_ok=True)
        except Exception:
            pass


@mcp.tool()
async def list_allowed_modules() -> dict:
    """列出沙箱允许使用的 Python 模块

    供 LLM 角色在编写代码前查询可用工具集。

    Returns:
        {
            "modules": ["math", "json", ...],
            "builtins": ["print", "len", "range", ...],
        }
    """
    return {
        "modules": [
            "math", "json", "statistics", "datetime",
            "random", "re", "itertools", "collections",
        ],
        "builtins": [
            "print", "len", "range", "enumerate", "zip", "map", "filter",
            "sum", "min", "max", "abs", "round", "sorted", "reversed",
            "isinstance", "type", "list", "dict", "set", "tuple", "str",
            "int", "float", "bool", "format", "repr",
        ],
        "limits": {
            "max_code_length": MAX_CODE_LENGTH,
            "max_timeout": MAX_TIMEOUT_SECONDS,
        },
    }


if __name__ == "__main__":
    mcp.run()
