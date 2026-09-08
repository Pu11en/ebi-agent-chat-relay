"""Regression checks for the owner auto-merge control-plane workflow."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _job_block(workflow: str, job: str, next_job: str) -> str:
    """Return one top-level job block without adding a YAML test dependency."""
    start = workflow.index(f"  {job}:")
    end = workflow.index(f"  {next_job}:", start)
    return workflow[start:end]


def test_owner_auto_merge_does_not_use_the_self_hosted_ci_pool() -> None:
    """A merge-polling job must not occupy the only runner required by CI."""
    workflow = (REPO_ROOT / ".github/workflows/auto-approve.yml").read_text()
    owner_job = _job_block(workflow, "auto-approve-and-merge", "auto-merge-dependabot")

    assert "runs-on: ubuntu-latest" in owner_job
    assert "self-hosted" not in owner_job


def test_owner_auto_merge_follows_the_current_repository_owner() -> None:
    workflow = (REPO_ROOT / ".github/workflows/auto-approve.yml").read_text()
    owner_job = _job_block(workflow, "auto-approve-and-merge", "auto-merge-dependabot")

    assert "github.event.pull_request.user.login == github.repository_owner" in owner_job
    assert "vars.OWNER_AUTO_MERGE_ENABLED == 'true'" in owner_job
    assert "ebibibi" not in owner_job


def test_private_post_merge_automation_is_opt_in() -> None:
    workflow = (REPO_ROOT / ".github/workflows/auto-approve.yml").read_text()
    owner_job = _job_block(workflow, "auto-approve-and-merge", "auto-merge-dependabot")

    assert "vars.AUTO_VERSION_BUMP_ENABLED" in owner_job
    assert '[ -n "$WEBHOOK_URL" ]' in owner_job


def test_product_verification_uses_github_hosted_runners() -> None:
    for path in (REPO_ROOT / ".github/workflows").glob("*.yml"):
        workflow = path.read_text()
        assert "runs-on: ubuntu-latest" in workflow
        assert "self-hosted" not in workflow
