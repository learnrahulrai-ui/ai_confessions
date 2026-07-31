from __future__ import annotations

import base64
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import AgentboxConfig, InstanceRecord
from .errors import AgentboxError
from .runner import Runner


RUN_CODEX_SCRIPT = r"""#!/usr/bin/env bash
set -uo pipefail
project_dir="$1"
cd "$project_dir"
mkdir -p .agentbox
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  branch="agentbox/task-$(date +%Y%m%d-%H%M%S)"
  git switch -c "$branch" > .agentbox/GIT_BRANCH.txt 2>&1 || {
    git branch --show-current > .agentbox/GIT_BRANCH.txt 2>&1 || true
  }
fi
set +e
codex exec --sandbox workspace-write \
  -o .agentbox/CODEX_RESULT.txt \
  "$(cat TASK.txt)" \
  > .agentbox/CODEX_RUN.log 2>&1
status=$?
set -e
printf '%s\n' "$status" > .agentbox/CODEX_EXIT_CODE.txt
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git status --short > .agentbox/GIT_STATUS.txt 2>&1 || true
  git diff --stat > .agentbox/GIT_DIFF_STAT.txt 2>&1 || true
  git diff > .agentbox/GIT_DIFF.patch 2>&1 || true
fi
"""


DEFAULT_EXCLUDES = [
    ".agentbox",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    "node_modules",
    "build",
    "dist",
    "credentials",
    "secrets",
]


class RemoteClient:
    def __init__(self, config: AgentboxConfig, runner: Runner) -> None:
        self.config = config
        self.runner = runner

    def _target(self, record: InstanceRecord) -> str:
        if not record.public_ip:
            raise AgentboxError(
                "Machine has no public IP in local state; run agentbox status first"
            )
        return f"{self.config.ssh.user}@{record.public_ip}"

    def _ssh_base(self, record: InstanceRecord, *, tty: bool = False) -> list[str]:
        args = [
            "ssh",
            "-i",
            str(self.config.ssh.private_key),
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "ServerAliveInterval=30",
        ]
        if tty:
            args.append("-t")
        args.append(self._target(record))
        return args

    def wait_for_ssh(
        self, record: InstanceRecord, attempts: int = 60, delay_seconds: int = 5
    ) -> None:
        for _ in range(attempts):
            result = self.runner.run(
                [*self._ssh_base(record), "test -f /var/lib/agentbox-ready"],
                capture=True,
                check=False,
            )
            if result.returncode == 0:
                return
            time.sleep(delay_seconds)
        raise AgentboxError("SSH or cloud-init did not become ready")

    def interactive_ssh(self, record: InstanceRecord) -> None:
        self.runner.exec_interactive(self._ssh_base(record))

    def run(
        self,
        record: InstanceRecord,
        command: str,
        *,
        tty: bool = False,
        capture: bool = True,
    ) -> str:
        result = self.runner.run(
            [*self._ssh_base(record, tty=tty), command], capture=capture
        )
        return result.stdout

    def project_path(self, project_name: str) -> str:
        clean = Path(project_name).name
        if clean in {"", ".", ".."}:
            raise AgentboxError("Invalid project name")
        return f"{self.config.workspace}/{clean}"

    def _ignore_file(self, project: Path) -> Path:
        generated = self.config.state_file.parent / "transfer-ignore.txt"
        generated.parent.mkdir(parents=True, exist_ok=True)
        lines = list(DEFAULT_EXCLUDES)
        custom = project / ".agentboxignore"
        if custom.exists():
            lines.extend(custom.read_text(encoding="utf-8").splitlines())
        generated.write_text("\n".join(dict.fromkeys(lines)) + "\n", encoding="utf-8")
        return generated

    def upload_command(
        self, record: InstanceRecord, local_path: Path, *, dry_run: bool = False
    ) -> tuple[list[str], str]:
        local_path = local_path.expanduser().resolve()
        if not local_path.is_dir():
            raise AgentboxError(f"Project folder does not exist: {local_path}")
        if not self.runner.which("rsync"):
            raise AgentboxError(
                "rsync is required for safe uploads because scp cannot apply secret exclusions"
            )
        remote_path = self.project_path(local_path.name)
        args = [
            "rsync",
            "-az",
            "--itemize-changes",
            "--exclude-from",
            str(self._ignore_file(local_path)),
            "-e",
            f"ssh -i {shlex.quote(str(self.config.ssh.private_key))} -o StrictHostKeyChecking=accept-new",
        ]
        if dry_run:
            args.append("--dry-run")
        args.extend(
            [
                str(local_path) + "/",
                f"{self._target(record)}:{remote_path}/",
            ]
        )
        return args, remote_path

    def upload(
        self, record: InstanceRecord, local_path: Path, *, dry_run: bool = False
    ) -> dict[str, str]:
        args, remote_path = self.upload_command(record, local_path, dry_run=dry_run)
        if not dry_run:
            self.run(record, f"mkdir -p {shlex.quote(remote_path)}")
        result = self.runner.run(args, capture=True)
        return {
            "remote_path": remote_path,
            "dry_run": str(dry_run).lower(),
            "changes": result.stdout.rstrip(),
            "command": self.runner.format_command(args),
        }

    def download_command(
        self,
        record: InstanceRecord,
        project_name: str,
        target: Path,
        *,
        dry_run: bool = False,
    ) -> list[str]:
        if not self.runner.which("rsync"):
            raise AgentboxError("rsync is required for downloads")
        remote_path = self.project_path(project_name)
        args = [
            "rsync",
            "-az",
            "--itemize-changes",
            "-e",
            f"ssh -i {shlex.quote(str(self.config.ssh.private_key))} -o StrictHostKeyChecking=accept-new",
        ]
        if dry_run:
            args.append("--dry-run")
        args.extend([f"{self._target(record)}:{remote_path}/", str(target) + "/"])
        return args

    def download(
        self,
        record: InstanceRecord,
        project_name: str,
        destination: Path,
        *,
        dry_run: bool = False,
    ) -> dict[str, str]:
        destination = destination.expanduser().resolve()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = destination / f"{Path(project_name).name}-{timestamp}"
        if target.exists():
            raise AgentboxError(f"Download target already exists: {target}")
        if not dry_run:
            target.mkdir(parents=True, exist_ok=False)
        args = self.download_command(
            record, project_name, target, dry_run=dry_run
        )
        result = self.runner.run(args, capture=True)
        return {
            "downloaded_to": str(target),
            "dry_run": str(dry_run).lower(),
            "changes": result.stdout.rstrip(),
            "command": self.runner.format_command(args),
        }

    def codex_login(self, record: InstanceRecord) -> None:
        self.runner.exec_interactive(
            [*self._ssh_base(record, tty=True), "codex login --device-auth"]
        )

    def codex_remote_command(
        self, project_path: str, task_file: Path, session: str
    ) -> str:
        encoded_script = base64.b64encode(RUN_CODEX_SCRIPT.encode("utf-8")).decode(
            "ascii"
        )
        encoded_task = base64.b64encode(task_file.read_bytes()).decode("ascii")
        script_path = f"{project_path}/.agentbox/run_codex.sh"
        task_path = f"{project_path}/TASK.txt"
        inner = f"bash {shlex.quote(script_path)} {shlex.quote(project_path)}"
        commands = [
            f"test -d {shlex.quote(project_path)}",
            f"mkdir -p {shlex.quote(project_path)}/.agentbox",
            f"printf %s {shlex.quote(encoded_script)} | base64 -d > {shlex.quote(script_path)}",
            f"chmod 700 {shlex.quote(script_path)}",
            f"printf %s {shlex.quote(encoded_task)} | base64 -d > {shlex.quote(task_path)}",
            f"tmux new-session -d -s {shlex.quote(session)} {shlex.quote(inner)}",
            f"printf %s {shlex.quote(session)} > {shlex.quote(project_path)}/.agentbox/TMUX_SESSION.txt",
        ]
        return "set -e; " + "; ".join(commands)

    def start_codex(
        self,
        record: InstanceRecord,
        project_name: str,
        task_file: Path,
        *,
        dry_run: bool = False,
    ) -> dict[str, str]:
        task_file = task_file.expanduser().resolve()
        if not task_file.is_file():
            raise AgentboxError(f"Task file does not exist: {task_file}")
        project_path = self.project_path(project_name)
        session = f"codex-{int(time.time())}"
        remote_command = self.codex_remote_command(project_path, task_file, session)
        full_command = [*self._ssh_base(record), remote_command]
        if not dry_run:
            self.runner.run(full_command)
        return {
            "tmux_session": session,
            "project_path": project_path,
            "dry_run": str(dry_run).lower(),
            "command": self.runner.format_command(full_command),
        }

    def logs(self, record: InstanceRecord, project_name: str, lines: int = 100) -> str:
        path = self.project_path(project_name)
        return self.run(
            record,
            f"tail -n {int(lines)} {shlex.quote(path)}/.agentbox/CODEX_RUN.log 2>/dev/null || true",
        )

    def result(self, record: InstanceRecord, project_name: str) -> str:
        path = self.project_path(project_name)
        return self.run(
            record,
            f"cat {shlex.quote(path)}/.agentbox/CODEX_RESULT.txt 2>/dev/null || true",
        )

    def diff(self, record: InstanceRecord, project_name: str) -> str:
        path = self.project_path(project_name)
        return self.run(
            record,
            f"cd {shlex.quote(path)} && git status --short && git diff --stat && git diff",
        )

    def jobs(self, record: InstanceRecord) -> str:
        return self.run(record, "tmux list-sessions 2>/dev/null || true")

    def cancel(self, record: InstanceRecord, project_name: str) -> dict[str, str]:
        path = self.project_path(project_name)
        command = (
            f"session=$(cat {shlex.quote(path)}/.agentbox/TMUX_SESSION.txt 2>/dev/null || true); "
            "if [ -n \"$session\" ]; then tmux kill-session -t \"$session\" 2>/dev/null || true; fi; "
            "printf '%s' \"$session\""
        )
        session = self.run(record, command).strip()
        return {"cancelled_session": session or "none"}
