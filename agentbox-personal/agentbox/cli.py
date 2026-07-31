from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, load_config, write_template
from .errors import AgentboxError
from .oci import OciClient
from .remote import RemoteClient
from .runner import Runner


def _add_dry_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentbox")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--json", action="store_true", dest="as_json")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-config")
    sub.add_parser("doctor")
    sub.add_parser("cost-check")
    sub.add_parser("setup-info")
    sub.add_parser("discover-image")

    create = sub.add_parser("create")
    create.add_argument("name")
    _add_dry_run(create)

    sub.add_parser("list")

    status = sub.add_parser("status")
    status.add_argument("name")

    ssh = sub.add_parser("ssh")
    ssh.add_argument("name")

    upload = sub.add_parser("upload")
    upload.add_argument("name")
    upload.add_argument("folder", type=Path)
    _add_dry_run(upload)

    download = sub.add_parser("download")
    download.add_argument("name")
    download.add_argument("project")
    download.add_argument(
        "destination", type=Path, nargs="?", default=Path("agentbox-results")
    )
    _add_dry_run(download)

    run = sub.add_parser("run")
    run.add_argument("name")
    run.add_argument("remote_command")

    login = sub.add_parser("codex-login")
    login.add_argument("name")

    codex = sub.add_parser("codex")
    codex.add_argument("name")
    codex.add_argument("project")
    codex.add_argument("task_file", type=Path)
    _add_dry_run(codex)

    logs = sub.add_parser("logs")
    logs.add_argument("name")
    logs.add_argument("project")
    logs.add_argument("--lines", type=int, default=100)

    result = sub.add_parser("result")
    result.add_argument("name")
    result.add_argument("project")

    diff = sub.add_parser("diff")
    diff.add_argument("name")
    diff.add_argument("project")

    cancel = sub.add_parser("cancel")
    cancel.add_argument("name")
    cancel.add_argument("project")

    jobs = sub.add_parser("jobs")
    jobs.add_argument("name")

    for action in ("stop", "start"):
        item = sub.add_parser(action)
        item.add_argument("name")
        _add_dry_run(item)

    delete = sub.add_parser("delete")
    delete.add_argument("name")
    delete.add_argument("--yes", action="store_true")
    _add_dry_run(delete)

    return parser


def _print(value: object, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, default=str))
        return
    if isinstance(value, list):
        if not value:
            print("No machines")
            return
        for row in value:
            if isinstance(row, dict):
                print(
                    f"{row.get('name','')}\t{row.get('state','')}\t{row.get('public_ip','')}"
                )
            else:
                print(row)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            print(f"{key}: {item}")
        return
    if value is not None:
        print(value)


def _doctor(config_path: Path, runner: Runner) -> dict[str, str]:
    programs = ["oci", "ssh", "rsync"]
    result = {program: runner.which(program) or "missing" for program in programs}
    result["config"] = str(config_path.expanduser().resolve())
    if config_path.expanduser().exists():
        try:
            config = load_config(config_path)
            result["config_values"] = "complete"
            result["ssh_private_key"] = (
                "present" if config.ssh.private_key.exists() else "missing"
            )
            result["ssh_public_key"] = (
                "present" if config.ssh.public_key.exists() else "missing"
            )
        except AgentboxError as exc:
            result["config_values"] = f"incomplete: {exc}"
    else:
        result["config_values"] = "missing"
    return result


def _confirm_delete(name: str) -> None:
    answer = input(f"Type delete-{name} to permanently delete VM and boot disk: ")
    if answer != f"delete-{name}":
        raise AgentboxError("Deletion cancelled")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runner = Runner()
    try:
        if args.command == "init-config":
            path = write_template(args.config)
            _print({"config_created": str(path)}, args.as_json)
            return 0
        if args.command == "doctor":
            _print(_doctor(args.config, runner), args.as_json)
            return 0

        config = load_config(args.config)
        oci = OciClient(config, runner)
        remote = RemoteClient(config, runner)

        if args.command == "cost-check":
            oci.validate_free_tier()
            _print(
                {
                    "shape": config.oracle.shape,
                    "ocpus": config.oracle.ocpus,
                    "memory_gb": config.oracle.memory_gb,
                    "boot_volume_gb": config.oracle.boot_volume_gb,
                    "guard": "accepted",
                },
                args.as_json,
            )
        elif args.command == "setup-info":
            _print(oci.setup_commands(), args.as_json)
        elif args.command == "discover-image":
            _print(oci.discover_image(), args.as_json)
        elif args.command == "create":
            if args.dry_run:
                _print(oci.preview_create(args.name), args.as_json)
            else:
                record = oci.create(args.name)
                remote.wait_for_ssh(record)
                _print(record.__dict__, args.as_json)
        elif args.command == "list":
            _print(oci.list(), args.as_json)
        elif args.command == "status":
            _print(oci.status(args.name), args.as_json)
        elif args.command == "ssh":
            remote.interactive_ssh(oci.record(args.name))
        elif args.command == "upload":
            _print(
                remote.upload(
                    oci.record(args.name), args.folder, dry_run=args.dry_run
                ),
                args.as_json,
            )
        elif args.command == "download":
            _print(
                remote.download(
                    oci.record(args.name),
                    args.project,
                    args.destination,
                    dry_run=args.dry_run,
                ),
                args.as_json,
            )
        elif args.command == "run":
            _print(
                remote.run(oci.record(args.name), args.remote_command).rstrip(),
                args.as_json,
            )
        elif args.command == "codex-login":
            remote.codex_login(oci.record(args.name))
        elif args.command == "codex":
            _print(
                remote.start_codex(
                    oci.record(args.name),
                    args.project,
                    args.task_file,
                    dry_run=args.dry_run,
                ),
                args.as_json,
            )
        elif args.command == "logs":
            _print(
                remote.logs(
                    oci.record(args.name), args.project, args.lines
                ).rstrip(),
                args.as_json,
            )
        elif args.command == "result":
            _print(
                remote.result(oci.record(args.name), args.project).rstrip(),
                args.as_json,
            )
        elif args.command == "diff":
            _print(
                remote.diff(oci.record(args.name), args.project).rstrip(),
                args.as_json,
            )
        elif args.command == "cancel":
            _print(
                remote.cancel(oci.record(args.name), args.project), args.as_json
            )
        elif args.command == "jobs":
            _print(remote.jobs(oci.record(args.name)).rstrip(), args.as_json)
        elif args.command == "stop":
            _print(
                oci.action(args.name, "STOP", dry_run=args.dry_run), args.as_json
            )
        elif args.command == "start":
            value = oci.action(args.name, "START", dry_run=args.dry_run)
            if not args.dry_run:
                remote.wait_for_ssh(oci.record(args.name))
            _print(value, args.as_json)
        elif args.command == "delete":
            if not args.dry_run and not args.yes:
                _confirm_delete(args.name)
            _print(oci.delete(args.name, dry_run=args.dry_run), args.as_json)
        else:
            raise AgentboxError(f"Unsupported command: {args.command}")
        return 0
    except AgentboxError as exc:
        print(f"agentbox: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("agentbox: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
