"""Validate operator documentation without Docker startup, SSH or live secrets."""

import argparse
import base64
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = [
    ROOT / "README.md",
    ROOT / "apps/README.md",
    ROOT / "infra/compose/README.md",
    *[
        ROOT / name
        for name in (
            "docs/authentication.md",
            "docs/admin-web.md",
            "docs/client-portal.md",
            "docs/architecture/README.md",
            "docs/planning/mvp.md",
            "docs/adr/README.md",
            "docs/adr/0017-client-cookie-session.md",
            "docs/runbooks/control-plane-deployment.md",
            "docs/node-bootstrap.md",
            "docs/operational-alerts.md",
        )
    ],
    *sorted((ROOT / "docs/guides").glob("*.md")),
]


def anchors(content):
    result = set()
    seen = {}
    for heading in re.findall(r"^#{1,6}\s+(.+)$", content, re.MULTILINE):
        slug = "".join(
            char
            for char in heading.lower().replace(" ", "-")
            if char in "-_" or unicodedata.category(char)[0] in "LN"
        )
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        result.add(slug + (f"-{count}" if count else ""))
    return result


def validate_links():
    count = 0
    for path in DOCUMENTS:
        content = path.read_text(encoding="utf-8")
        assert content.count("```") % 2 == 0, f"Unclosed fence: {path}"
        for target in re.findall(r"\[[^\]\n]+\]\(([^)]+)\)", content):
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith("//"):
                continue
            destination = path.parent / unquote(parsed.path) if parsed.path else path
            assert destination.is_file(), f"Missing link: {path.relative_to(ROOT)} -> {target}"
            if parsed.fragment:
                assert unquote(parsed.fragment) in anchors(
                    destination.read_text(encoding="utf-8")
                ), f"Missing anchor: {path.relative_to(ROOT)} -> {target}"
            count += 1
    print(f"PASS: {count} local links/anchors in {len(DOCUMENTS)} documents")


def run(command, *, stdin=None):
    env = os.environ.copy()
    modules = Path(command[0]).parent / "Modules"
    if Path(command[0]).name.lower() in ("powershell.exe", "pwsh.exe") and modules.is_dir():
        # A parent running a different PowerShell version may export incompatible modules.
        env["PSModulePath"] = str(modules)
    # Preserve LF for shell here-documents on Windows pipes.
    result = subprocess.run(
        command,
        input=stdin.encode("utf-8") if stdin is not None else None,
        capture_output=True,
        env=env,
    )
    # Never print child output: a failed secret-generation command might include values.
    if result.returncode:
        safe_error = re.search(rb"DOCERROR:[A-Za-z0-9.]+:[0-9]+:[A-Za-z0-9.,_-]+", result.stderr)
        detail = safe_error.group().decode() if safe_error else "No safe diagnostic"
        raise AssertionError(f"Validation subprocess failed: {Path(command[0]).name}; {detail}")


def validate_env(path):
    values = dict(line.split("=", 1) for line in path.read_text(encoding="utf-8-sig").splitlines())
    passwords = [
        values[key] for key in ("POSTGRES_PASSWORD", "REDIS_PASSWORD", "GRAFANA_ADMIN_PASSWORD")
    ]
    assert len(set(passwords)) == 3
    assert all(re.fullmatch(r"[a-f0-9]{64}", password) for password in passwords)
    assert len(base64.urlsafe_b64decode(values["TTCP_AUTH_ENCRYPTION_KEY"])) == 32
    assert values["POSTGRES_DB"] == "ttcp"


def validate_no_overwrite(command, path):
    original = path.read_bytes()
    try:
        run(command)
    except AssertionError:
        assert path.read_bytes() == original, "Existing env changed"
    else:
        raise AssertionError("Env writer did not refuse an existing file")


def validate_compose_flags(executable, blocks):
    commands = {}
    for language, body in blocks:
        if language != "text":
            continue
        for line in body.splitlines():
            tokens = shlex.split(line)
            if not tokens or tokens[0] != "dc":
                continue
            tokens = tokens[1:]
            if tokens[:2] == ["--profile", "tools"]:
                tokens = tokens[2:]
            commands.setdefault(tokens[0], set()).update(
                token.split("=", 1)[0] for token in tokens[1:] if token.startswith("--")
            )
    for command, flags in commands.items():
        # Call only help, never the documented startup/exec command itself.
        result = subprocess.run([executable, command, "--help"], capture_output=True)
        assert result.returncode == 0, f"Compose help failed for {command}"
        help_text = result.stdout.decode("utf-8", errors="replace")
        assert all(flag in help_text for flag in flags), f"Unknown Compose flag for {command}"
    print(f"PASS: Compose CLI help confirms commands/long flags in {len(commands)} command groups")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bash", help="Bash executable for syntax-only parsing")
    parser.add_argument(
        "--powershell", help="PowerShell executable for parsing and disposable env QA"
    )
    parser.add_argument("--compose-executable", help="Standalone Compose for read-only CLI help QA")
    args = parser.parse_args()
    validate_links()
    guide = (ROOT / "docs/guides/deployment-guide.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```(\w+)\n(.*?)\n```", guide, re.DOTALL)
    if args.compose_executable:
        validate_compose_flags(args.compose_executable, blocks)
    (ROOT / ".tools").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="docs022-", dir=ROOT / ".tools") as directory:
        work = Path(directory)
        bash_blocks = [body for language, body in blocks if language == "bash"]
        if args.bash:
            for body in bash_blocks:
                run([args.bash, "-n"], stdin=body)
            print(f"PASS: Bash syntax, {len(bash_blocks)} blocks; no commands executed")
        else:
            print("SKIP: Bash parser not supplied")
        python_body = re.search(r"<<'PY'\n(.*?)\nPY", guide, re.DOTALL).group(1)
        compile(python_body, "deployment-guide.md heredoc", "exec")
        python_env = work / "python.env"
        run([sys.executable, "-c", python_body, str(python_env)])
        validate_env(python_env)
        validate_no_overwrite([sys.executable, "-c", python_body, str(python_env)], python_env)
        print(
            "PASS: Linux env writer, disposable random values, distinct passwords and Fernet format"
        )
        ps_blocks = [body for language, body in blocks if language == "powershell"]
        if args.powershell:
            for index, body in enumerate(ps_blocks):
                source = work / f"block-{index}.ps1"
                source.write_text(body, encoding="utf-8-sig")
                escaped = str(source).replace("'", "''")
                parse = (
                    "$tokens=$null; $errors=$null; "
                    f"[System.Management.Automation.Language.Parser]::ParseFile('{escaped}', "
                    "[ref]$tokens, [ref]$errors) | Out-Null; if($errors.Count) { exit 1 }"
                )
                run([args.powershell, "-NoProfile", "-NonInteractive", "-Command", parse])
            writer = next(body for body in ps_blocks if "function New-TtcpRandomBytes" in body)
            isolated = str(work / "secrets").replace("'", "''")
            writer = writer.replace("Join-Path $env:LOCALAPPDATA 'TTCP-first'", f"'{isolated}'")
            source = work / "env-writer.ps1"
            source.write_text(
                "$ErrorActionPreference='Stop'\ntry {\n"
                + writer
                + "\n} catch { [Console]::Error.WriteLine('DOCERROR:' + "
                "$_.Exception.GetType().FullName + ':' + $_.InvocationInfo.ScriptLineNumber "
                "+ ':' + $_.FullyQualifiedErrorId); exit 1 }",
                encoding="utf-8-sig",
            )
            run(
                [
                    args.powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    source.read_text(encoding="utf-8-sig"),
                ]
            )
            validate_env(work / "secrets/runtime.env")
            validate_no_overwrite(
                [
                    args.powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    source.read_text(encoding="utf-8-sig"),
                ],
                work / "secrets/runtime.env",
            )
            print(
                f"PASS: PowerShell syntax, {len(ps_blocks)} blocks; disposable env writer and ACL"
            )
        else:
            print("SKIP: PowerShell parser not supplied")
    print("No containers, migrations, SSH, bootstrap, live TLS or VPN operations executed")


if __name__ == "__main__":
    main()
