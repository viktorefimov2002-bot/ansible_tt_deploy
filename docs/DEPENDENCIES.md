# Controller dependencies

This project runs from a controller machine, for example your laptop, WSL environment, CI runner, or admin VM.

The controller needs a few tools before `./ttctl init`, `./ttctl deploy`, `./ttctl add-client`, and `./ttctl remove-client` can work.

## Required tools

Required in normal operation:

- `python3`
- `ansible`
- `ansible-playbook`
- Python module `yaml`, provided by `PyYAML` or a distro package such as `python3-yaml`

Required only when using SSH password authentication:

- `sshpass`

`ansible-playbook` is normally installed as part of the `ansible` package on common Linux distributions.

## Quick check

Run:

```bash
./scripts/install_requirements.sh --check-only
```

This checks:

- `python3`
- `ansible`
- `ansible-playbook`
- `sshpass`
- Python import `yaml`

If you use only SSH keys, missing `sshpass` may be acceptable. If you use `./ttctl init --config deploy.yml --ask-ssh-pass`, then `sshpass` is needed.

## Install dependencies

Interactive install:

```bash
./scripts/install_requirements.sh
```

Non-interactive install:

```bash
./scripts/install_requirements.sh --yes
```

Python-only install from `requirements.txt`:

```bash
./scripts/install_requirements.sh --python-only
```

System packages only:

```bash
./scripts/install_requirements.sh --system-only
```

Install Python dependencies into a project virtual environment:

```bash
./scripts/install_requirements.sh --venv
source .venv/bin/activate
```

Custom virtual environment path:

```bash
./scripts/install_requirements.sh --venv-dir ./venv
source ./venv/bin/activate
```

## Supported package managers

The helper currently supports:

- `apt-get`
- `dnf`
- `yum`
- `pacman`
- `zypper`

If your OS uses another package manager, install these manually:

```text
python3
python3-pip
ansible
sshpass
```

Then install Python requirements if needed:

```bash
python3 -m pip install -r requirements.txt
```

## Remove dependencies safely

Use the uninstall helper:

```bash
./scripts/uninstall_requirements.sh
```

It asks separately before removing:

- `ansible`
- `sshpass`
- `python3-pip` or `python-pip`
- `PyYAML` from the current Python environment
- optional project virtualenv

It intentionally does **not** remove `python3` automatically, because Python is often required by the OS and other tools.

Preview without removing anything:

```bash
./scripts/uninstall_requirements.sh --dry-run
```

Also consider removing only the project virtualenv:

```bash
./scripts/uninstall_requirements.sh --venv
```

## Why not put everything into requirements.txt?

`requirements.txt` is only for Python packages. In this project it contains:

```text
PyYAML>=6.0
```

Tools such as `python3`, `ansible`, `ansible-playbook`, and `sshpass` are system-level dependencies. Installing and removing them through the OS package manager is safer and more predictable than pretending they are Python requirements.

## Safety notes

Do not run the uninstall helper with aggressive flags on a shared admin machine unless you know those tools were installed only for this project.

Recommended cleanup order:

1. Run `./scripts/uninstall_requirements.sh --dry-run`.
2. Remove only project-specific virtualenv if used.
3. Remove `sshpass` if you no longer use password SSH.
4. Keep `python3` and often keep `ansible` if the machine is used for other infrastructure tasks.
