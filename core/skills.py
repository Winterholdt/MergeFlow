"""Modular Agentic Skill System for MergeFlow orchestrator.

Provides targeted, domain-specific best practice instructions to autonomous agents
during their respective workflow phases without polluting the context window with
irrelevant knowledge.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.config import SkillsConfig

logger = logging.getLogger(__name__)

DEFAULT_SKILLS_DIR = Path("skills")

# Built-in standard skill mappings for workflow phases
DEFAULT_PHASE_SKILLS: dict[str, list[str]] = {
    "discovery": ["discovery/requirements_engineering.md"],
    "dev": [
        "dev/clean_code_solid.md",
        "dev/test_driven_development.md",
        "dev/conventional_commits.md",
    ],
    "review_security": ["review/security_owasp.md"],
    "review_architecture": ["review/architecture_quality.md"],
    "review_qa": ["review/qa_coverage.md"],
    "review_functional": [
        "discovery/requirements_engineering.md",
        "dev/clean_code_solid.md",
    ],
}

# Normalization mapping for phase aliases
PHASE_ALIASES: dict[str, str] = {
    "spec_discovery": "discovery",
    "discovery": "discovery",
    "dev": "dev",
    "coding": "dev",
    "developer": "dev",
    "ci_healing": "dev",
    "ci_loop": "dev",
    "review_security": "review_security",
    "security": "review_security",
    "review_architecture": "review_architecture",
    "review_arch": "review_architecture",
    "architecture": "review_architecture",
    "review_qa": "review_qa",
    "review_qa_tests": "review_qa",
    "qa_testing": "review_qa",
    "qa": "review_qa",
    "review_functional": "review_functional",
    "functional": "review_functional",
}


def normalize_phase_name(phase: str) -> str:
    """Normalize phase string to canonical internal key."""
    cleaned = phase.strip().lower()
    return PHASE_ALIASES.get(cleaned, cleaned)


class SkillManager:
    """Manages loading, mapping, and prompt-injection of modular agent skills."""

    def __init__(
        self,
        skills_dir: Path | str | None = None,
        enabled: bool = True,
        phases_config: dict[str, list[str]] | None = None,
        config: SkillsConfig | None = None,
    ) -> None:
        """Initialize the SkillManager.

        Args:
            skills_dir: Optional custom skills root directory path.
            enabled: Whether skill injection is enabled.
            phases_config: Optional custom phase-to-skills file mapping.
            config: Optional SkillsConfig instance from application configuration.
        """
        if config is not None:
            self.enabled = config.enabled
            target_dir = Path(config.directory)
            if config.directory in ("./skills", "skills") and not target_dir.is_dir():
                package_skills = Path(__file__).resolve().parent.parent / "skills"
                self.skills_dir = package_skills.resolve() if package_skills.is_dir() else target_dir.resolve()
            else:
                self.skills_dir = target_dir.resolve()
            self.phases_config = dict(config.phases)
        else:
            self.enabled = enabled
            if skills_dir is not None:
                self.skills_dir = Path(skills_dir).resolve()
            else:
                local_skills = DEFAULT_SKILLS_DIR.resolve()
                if local_skills.is_dir():
                    self.skills_dir = local_skills
                else:
                    package_skills = Path(__file__).resolve().parent.parent / "skills"
                    self.skills_dir = package_skills.resolve()
            self.phases_config = dict(phases_config) if phases_config is not None else None

    def load_skill(self, skill_path: str) -> str:
        """Load the text content of a skill markdown file from the skills directory.

        Fails gracefully with a warning log and returns an empty string if the file
        does not exist or cannot be read.

        Args:
            skill_path: Relative or absolute path to the skill markdown file.

        Returns:
            Stripped text content of the skill file, or empty string if missing.
        """
        if not skill_path or not skill_path.strip():
            return ""

        path_obj = Path(skill_path)
        if path_obj.is_absolute():
            resolved_file = path_obj
        else:
            resolved_file = self.skills_dir / path_obj

        if not resolved_file.is_file():
            # Also try falling back to local relative path if skills_dir resolution missed
            alt_path = DEFAULT_SKILLS_DIR / path_obj
            if alt_path.is_file():
                resolved_file = alt_path
            else:
                logger.warning("Skill file not found: %s", resolved_file)
                return ""

        try:
            return resolved_file.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.warning("Error reading skill file '%s': %s", resolved_file, exc)
            return ""

    def get_skills_for_phase(
        self,
        phase: str,
        custom_skills: list[str] | None = None,
    ) -> str:
        """Retrieve and format a compact Markdown block of skills for a given phase.

        Maps workflow phases (e.g., DISCOVERY, DEV, REVIEW_SECURITY, REVIEW_ARCH,
        review_qa) to their configured or standard skill files. Merges their contents
        with clean section headers and dividers.

        Args:
            phase: Target workflow phase name.
            custom_skills: Optional additional project-specific skill file paths.

        Returns:
            Compact formatted Markdown string containing loaded skills, or empty
            string if skills are disabled or none could be loaded.
        """
        if not self.enabled:
            return ""

        canonical = normalize_phase_name(phase)
        raw_key = phase.strip().lower()

        # 1. Determine base skills from config or default mappings
        base_skills: list[str] = []
        if self.phases_config is not None:
            if raw_key in self.phases_config:
                base_skills = list(self.phases_config[raw_key])
            elif canonical in self.phases_config:
                base_skills = list(self.phases_config[canonical])
            elif canonical in DEFAULT_PHASE_SKILLS:
                base_skills = list(DEFAULT_PHASE_SKILLS[canonical])
        else:
            if canonical in DEFAULT_PHASE_SKILLS:
                base_skills = list(DEFAULT_PHASE_SKILLS[canonical])
            elif raw_key in DEFAULT_PHASE_SKILLS:
                base_skills = list(DEFAULT_PHASE_SKILLS[raw_key])

        # 2. Append custom skills dynamically if specified
        combined_skills = list(base_skills)
        if custom_skills:
            for skill in custom_skills:
                if skill and skill not in combined_skills:
                    combined_skills.append(skill)

        if not combined_skills:
            return ""

        # 3. Load contents for each skill
        loaded_sections: list[str] = []
        for skill_path in combined_skills:
            content = self.load_skill(skill_path)
            if content:
                # Format each skill with a clear header
                clean_name = Path(skill_path).stem.replace("_", " ").title()
                loaded_sections.append(
                    f"#### [Skill: {clean_name} ({skill_path})]\n\n{content}"
                )

        if not loaded_sections:
            return ""

        # 4. Join with clean markdown dividers
        divider = "\n\n---\n\n"
        skills_body = divider.join(loaded_sections)

        header = f"### Active Domain Skills & Guidelines ({phase.upper()})"
        return f"{header}\n\n{skills_body}"

    def inject_skills(
        self,
        prompt: str,
        phase: str,
        custom_skills: list[str] | None = None,
    ) -> str:
        """Inject the formatted skills block into a prompt template.

        Replaces the placeholder '{{PHASE_SKILLS}}' if present. If the placeholder
        is absent, preserves original prompt behavior without unrequested side effects.

        Args:
            prompt: Original prompt template string.
            phase: Target workflow phase identifier.
            custom_skills: Optional additional project-specific skill file paths.

        Returns:
            Prompt string with injected skills block or clean placeholder removal.
        """
        skills_block = self.get_skills_for_phase(phase, custom_skills=custom_skills)
        placeholder = "{{PHASE_SKILLS}}"

        if placeholder in prompt:
            return prompt.replace(placeholder, skills_block).strip()

        return prompt
