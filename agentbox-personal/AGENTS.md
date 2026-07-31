Agentbox is a one-person CLI, not a hosted SaaS.

Core path:
local command -> OCI CLI -> one Oracle Always Free A1 VM
local folder -> rsync and SSH -> /workspace/project
Codex -> tmux -> remote Git branch -> logs, result, and diff

Security rules:
Never add Oracle credentials, OpenAI authentication files, SSH private keys, .env files, or user secrets to this repository.
Never weaken the free-tier guard.
Never silently create a paid Oracle resource.
Never expose ports by default.
Never overwrite the original laptop project during download.
Do not exclude .git from transfers; Git history is required for review.

Implementation rules:
Python 3.11 standard library first.
Keep command construction independently testable.
Use explicit operational errors.
Use Oracle and OpenAI primary documentation for mutable command behavior.
Run: python -m unittest discover -s tests -v
Run: python -m compileall -q agentbox tests

Acceptance path:
create dry-run -> create -> upload dry-run -> upload -> codex-login -> codex dry-run -> codex -> logs -> result -> diff -> timestamped download -> stop -> start -> delete dry-run -> delete
