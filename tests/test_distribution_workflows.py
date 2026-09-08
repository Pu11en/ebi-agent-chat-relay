"""Release and packaging workflows remain portable and fail-safe."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def test_ci_builds_installable_package_and_container() -> None:
    workflow = (WORKFLOWS / "ci.yml").read_text()

    assert "  package:" in workflow
    assert "uv build" in workflow
    assert '"$RUNNER_TEMP/wheel-smoke/bin/pip" install dist/*.whl' in workflow
    assert "  container:" in workflow
    assert "deploy/teams-relay/Dockerfile" in workflow
    assert "push: false" in workflow


def test_release_publishes_assets_and_container_without_external_secrets() -> None:
    workflow = (WORKFLOWS / "release.yml").read_text()

    assert "release:" in workflow
    assert "types: [published]" in workflow
    assert "packages: write" in workflow
    assert "ghcr.io/${{ github.repository }}" in workflow
    assert "gh release upload" in workflow
    assert "actions/attest-build-provenance@v3" in workflow


def test_pypi_publish_is_trusted_and_explicitly_opt_in() -> None:
    workflow = (WORKFLOWS / "release.yml").read_text()
    pypi_job = workflow.split("  pypi:", 1)[1]

    assert "vars.PYPI_PUBLISHING_ENABLED == 'true'" in pypi_job
    assert "id-token: write" in pypi_job
    assert "pypa/gh-action-pypi-publish@release/v1" in pypi_job
    assert "password:" not in pypi_job
