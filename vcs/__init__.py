"""VCS (Version Control System) package for MergeFlow.

Provides local Git operations and remote VCS hosting wrappers (GitHub / GitLab).
"""

from vcs.git_handler import (
    GitCommandError,
    GitError,
    GitHandler,
    commit_changes,
    create_feature_branch,
    ensure_conventional_commit,
    ensure_repo,
    get_diff,
    get_recent_commits,
    init_repo,
    is_conventional_commit,
    is_repo,
    push_branch,
)
from vcs.ci_watcher import (
    CIWatcher,
    PipelineResult,
    extract_relevant_errors,
    filter_ci_noise,
    wait_for_pipeline,
)
from vcs.remote_vcs import (
    PipelineCheck,
    PipelineState,
    PipelineStatus,
    RemoteVCS,
    RemoteVCSError,
    VCSType,
    create_draft_pr,
    get_pipeline_status,
)

__all__ = [
    "GitError",
    "GitCommandError",
    "GitHandler",
    "create_feature_branch",
    "is_repo",
    "init_repo",
    "ensure_repo",
    "get_diff",
    "commit_changes",
    "push_branch",
    "ensure_conventional_commit",
    "is_conventional_commit",
    "PipelineCheck",
    "PipelineState",
    "PipelineStatus",
    "RemoteVCS",
    "RemoteVCSError",
    "VCSType",
    "create_draft_pr",
    "get_pipeline_status",
    "CIWatcher",
    "PipelineResult",
    "wait_for_pipeline",
    "extract_relevant_errors",
    "filter_ci_noise",
    "get_recent_commits",
]
