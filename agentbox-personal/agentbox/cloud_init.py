from __future__ import annotations


CLOUD_INIT = r"""#cloud-config
package_update: true
package_upgrade: false
packages:
  - build-essential
  - ca-certificates
  - cmake
  - curl
  - docker.io
  - git
  - jq
  - nodejs
  - npm
  - python3
  - python3-pip
  - rsync
  - tmux
  - unzip

write_files:
  - path: /etc/ssh/sshd_config.d/99-agentbox.conf
    permissions: '0644'
    content: |
      PasswordAuthentication no
      KbdInteractiveAuthentication no
      PermitRootLogin no

runcmd:
  - [mkdir, -p, /workspace]
  - [chown, -R, ubuntu:ubuntu, /workspace]
  - [usermod, -aG, docker, ubuntu]
  - [systemctl, enable, --now, docker]
  - [systemctl, restart, ssh]
  - [npm, install, -g, "@openai/codex@latest"]
  - [bash, -lc, "codex --version > /var/log/agentbox-codex-version.txt 2>&1 || true"]
  - [bash, -lc, "date -Is > /var/lib/agentbox-ready"]
final_message: "Agentbox machine initialization completed"
"""
