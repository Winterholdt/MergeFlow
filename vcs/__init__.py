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
    get_diff,
    is_conventional_commit,
    push_branch,
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
]
