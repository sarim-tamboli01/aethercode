import os
import shutil
import tempfile
from dataclasses import dataclass

import docker
from docker.errors import DockerException, ContainerError, ImageNotFound

DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_PYTHON_IMAGE = "aethercode-test-runner:latest"

# Resource limits — keep containers cheap and prevent runaway processes
# from a buggy or malicious-by-accident generated test.
MEMORY_LIMIT = "512m"
CPU_QUOTA = 50000   # 50% of one CPU core (quota is in units of 1/100000 CPU)
CPU_PERIOD = 100000


@dataclass
class ContainerRunResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str = ""


class DockerRunner:
    """
    Wraps Docker SDK operations for running code/tests in a sandboxed
    container. Each run gets a fresh container that's destroyed after,
    so there's no state leakage between test runs.
    """

    def __init__(self, image: str = DEFAULT_PYTHON_IMAGE):
        self.image = image
        try:
            self.client = docker.from_env()
        except DockerException as e:
            raise RuntimeError(
                "Could not connect to Docker. Is Docker Desktop running? "
                f"Original error: {e}"
            ) from e

    def ensure_image_available(self) -> None:
        """
        Pulls the configured image if it isn't already present locally.
        Called once before running tests, to avoid a slow pull mid-loop
        during the Debug & Repair Loop's repeated attempts.
        """
        try:
            self.client.images.get(self.image)
        except ImageNotFound:
            self.client.images.pull(self.image)

    def run_command(
        self,
        repo_path: str,
        command: str,
        requirements_file: str | None = "requirements.txt",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> ContainerRunResult:
        """
        Runs a shell command inside a fresh container, with the repo
        mounted read-write at /app. Optionally installs dependencies
        from a requirements file first.

        Args:
            repo_path: Local path to the repo (or a working copy of it)
                       to mount into the container.
            command: Shell command to run, e.g. "pytest -v".
            requirements_file: Relative path to a requirements file to
                                pip install before running the command.
                                Pass None to skip dependency installation.
            timeout_seconds: Max time to allow the container to run
                              before killing it and returning timed_out=True.

        Returns:
            ContainerRunResult with exit code, stdout, stderr.
        """
        self.ensure_image_available()

        full_command = self._build_shell_command(command, requirements_file)

        container = None
        try:
            container = self.client.containers.run(
                self.image,
                command=["sh", "-c", full_command],
                volumes={
                    os.path.abspath(repo_path): {"bind": "/app", "mode": "rw"}
                },
                working_dir="/app",
                mem_limit=MEMORY_LIMIT,
                cpu_quota=CPU_QUOTA,
                cpu_period=CPU_PERIOD,
                network_disabled=True,  # tests shouldn't need network; extra safety
                detach=True,
            )

            try:
                result = container.wait(timeout=timeout_seconds)
                exit_code = result.get("StatusCode", -1)
                timed_out = False
            except Exception:
                container.kill()
                exit_code = -1
                timed_out = True

            stdout = container.logs(stdout=True, stderr=False).decode(
                "utf-8", errors="ignore"
            )
            stderr = container.logs(stdout=False, stderr=True).decode(
                "utf-8", errors="ignore"
            )

            return ContainerRunResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                timed_out=timed_out,
            )

        except ContainerError as e:
            return ContainerRunResult(
                exit_code=e.exit_status,
                stdout="",
                stderr=str(e),
                error=f"Container error: {e}",
            )
        except DockerException as e:
            return ContainerRunResult(
                exit_code=-1,
                stdout="",
                stderr="",
                error=f"Docker error: {e}",
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except DockerException:
                    pass  # already removed or never fully created

    def _build_shell_command(
        self, command: str, requirements_file: str | None
    ) -> str:
        """
        Builds the full shell command run inside the container: optional
        pip install step, then the actual test command. Combined into
        one shell invocation to avoid the overhead of multiple containers.
        """
        parts = []
        if requirements_file:
            # '|| true' so a missing/empty requirements file doesn't
            # fail the whole run — not every step touches dependencies.
            parts.append(
                f"if [ -f {requirements_file} ]; then "
                f"pip install --no-cache-dir -q -r {requirements_file} || true; "
                f"fi"
            )
        parts.append(command)
        return " && ".join(parts)


def create_isolated_copy(repo_path: str) -> str:
    """
    Creates a temporary copy of the repo so container runs (which may
    write files, e.g. .pyc, __pycache__, test artifacts) never mutate
    the agent's working clone. Caller is responsible for cleanup via
    cleanup_isolated_copy().
    """
    temp_path = tempfile.mkdtemp(prefix="aethercode_docker_run_")
    shutil.copytree(repo_path, temp_path, dirs_exist_ok=True)
    return temp_path


def cleanup_isolated_copy(temp_path: str) -> None:
    """Removes a temporary isolated copy created by create_isolated_copy()."""
    if os.path.exists(temp_path):
        shutil.rmtree(temp_path, ignore_errors=True)