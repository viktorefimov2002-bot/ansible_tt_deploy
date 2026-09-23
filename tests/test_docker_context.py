"""Every Ansible file copied into the worker image must survive .dockerignore."""

import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_worker_ansible_copy_sources_are_in_build_context():
    dockerfile = (ROOT / "apps/Dockerfile").read_text()
    rules = set((ROOT / ".dockerignore").read_text().splitlines())
    sources = [
        shlex.split(line)[1]
        for line in dockerfile.splitlines()
        if line.startswith("COPY automation/ansible/")
    ]
    assert sources
    for source in sources:
        path = ROOT / source
        assert path.is_file(), source
        assert f"!{source}" in rules, source
        assert "!automation/" in rules
        assert "!automation/ansible/" in rules
        for parent in Path(source).parents:
            if str(parent).replace("\\", "/") == "automation/ansible/group_vars":
                assert "!automation/ansible/group_vars/" in rules
