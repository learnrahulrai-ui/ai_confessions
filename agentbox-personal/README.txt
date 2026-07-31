agentbox-personal 0.2

Laptop project -> rsync over SSH -> Oracle Always Free A1 VM -> Codex in tmux -> Git diff -> timestamped laptop result

Agentbox is a one-person Machine0-style command line program. It creates and controls one Oracle Cloud VM, copies a selected project, starts Codex inside a detached tmux session, exposes logs and Git changes, and copies results into a new laptop directory.

Nothing in this repository contains Oracle credentials, an OpenAI login, or an SSH private key.

Cost barrier

Agentbox accepts only:

VM.Standard.A1.Flex
maximum 2 OCPUs
maximum 12 GB RAM
50 to 200 GB boot disk

The guard prevents Agentbox from requesting a different shape or larger configured resources. It cannot inspect unrelated resources in your Oracle account. Oracle capacity and continued Always Free eligibility are controlled by Oracle.

Windows path

Windows -> WSL Ubuntu -> Agentbox -> OCI CLI and SSH -> Oracle VM

Install WSL prerequisites

sudo apt update
sudo apt install -y python3 python3-venv python3-pip openssh-client rsync

Install Oracle OCI CLI using Oracle's supported installer or package instructions. Confirm:

oci --version
ssh -V
rsync --version

Install Agentbox

cd agentbox-personal
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e . --no-build-isolation
python -m unittest discover -s tests -v
agentbox --help

Oracle authentication

Run locally inside WSL:

oci setup config

This writes Oracle authentication under ~/.oci. Never add that directory to GitHub or a Codex cloud environment.

SSH key

ssh-keygen -t ed25519 -f ~/.ssh/agentbox_ed25519

Configuration

agentbox init-config

Edit ~/.agentbox/config.toml.

Required values:

compartment_id
availability_domain
subnet_id

image_id may remain blank. Agentbox then requests the newest available Canonical Ubuntu image compatible with VM.Standard.A1.Flex.

Read-only discovery

agentbox setup-info
agentbox discover-image
agentbox doctor
agentbox cost-check

First dry run

agentbox create devbox --dry-run

The output prints the exact OCI launch command but does not create a resource.

Create VM

agentbox create devbox

The command waits for Oracle state RUNNING, then waits until SSH and cloud-init are ready. Cloud-init installs Git, C++, CMake, Python, Node, npm, Docker, rsync, tmux, and Codex CLI.

Project upload preview

agentbox upload devbox /mnt/c/code/my_project --dry-run

Review listed files. Agentbox excludes .env files, private-key patterns, credentials, secrets, node_modules, build output, and previous Agentbox output. It does not exclude .git because Git history and diffs are required.

Upload

agentbox upload devbox /mnt/c/code/my_project

Codex login

agentbox codex-login devbox

SSH opens an interactive Codex device-login flow on the Oracle VM. The resulting Codex credential exists on that VM, not in this repository.

Prepare work

Copy examples/TASK.txt and replace its requested-work paragraph with one concrete task.

Preview detached execution

agentbox codex devbox my_project TASK.txt --dry-run

Start detached execution

agentbox codex devbox my_project TASK.txt

The laptop can disconnect after the command succeeds. Oracle continues running tmux and Codex.

Inspect later

agentbox jobs devbox
agentbox logs devbox my_project --lines 200
agentbox result devbox my_project
agentbox diff devbox my_project

Cancel

agentbox cancel devbox my_project

Download preview

agentbox download devbox my_project ./agentbox-results --dry-run

Download

agentbox download devbox my_project ./agentbox-results

Agentbox creates a new directory such as agentbox-results/my_project-20260731-235959. It does not overwrite the original laptop project.

Power control

agentbox stop devbox --dry-run
agentbox stop devbox
agentbox start devbox

Permanent deletion

agentbox delete devbox --dry-run
agentbox delete devbox

Without --yes, deletion requires typing delete-devbox. Agentbox asks Oracle to terminate the VM and delete its boot disk.

Acceptance proof

1. Upload a Git repository.
2. Start Codex with a small task.
3. Disconnect the laptop.
4. Reconnect later.
5. Read result and diff.
6. Download into a timestamped directory.
7. Stop and start the VM.
8. Confirm remote project files remain.
9. Delete only after the result exists locally or in GitHub.

Current boundary

Unit-tested command construction is complete.
No live Oracle account has been used from this environment.
The first real provisioning run requires your local Oracle login, compartment, subnet, account capacity, and explicit approval.
