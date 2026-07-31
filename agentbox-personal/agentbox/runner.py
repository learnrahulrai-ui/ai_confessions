from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import Mapping, Sequence

from .errors import AgentboxError


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    stdout: str
    stderr: str
    returncode: int


class Runner:
    def which(self, executable: str) -> str | None:
        return shutil.which(executable)

    @staticmethod
    def format_command(args: Sequence[str]) -> str:
        return shlex.join([str(value) for value in args])

    def run(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        capture: bool = True,
        check: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        completed = subprocess.run(
            list(args),
            input=input_text,
            text=True,
            capture_output=capture,
            env=dict(os.environ, **(dict(env) if env else {})),
            check=False,
        )
        result = CommandResult(
            args=tuple(str(value) for value in args),
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            returncode=completed.returncode,
        )
        if check and completed.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "command failed"
            raise AgentboxError(
                f"Command failed: {self.format_command(result.args)}\n{detail}"
            )
        return result

    def exec_interactive(self, args: Sequence[str]) -> None:
        executable = self.which(str(args[0]))
        if executable is None:
            raise AgentboxError(f"Required program is missing: {args[0]}")
        os.execv(executable, [executable, *[str(value) for value in args[1:]]])
