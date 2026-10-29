"""Unit tests for the Modular Agentic Skill System."""

from pathlib import Path
import pytest

from core.config import AppConfig, SkillsConfig, load_config
from core.discovery import load_discovery_prompt
from core.dev_loop import load_developer_prompt
from core.healing_loop import load_ci_healing_prompt
from core.reviewer import load_reviewer_prompt
from core.skills import (
    DEFAULT_PHASE_SKILLS,
    SkillManager,
    normalize_phase_name,
)


# ============================================================================
# 1. INITIALIZATION & NORMALIZATION TESTS
# ============================================================================

def test_phase_name_normalization():
    """Verify phase name normalization and alias mappings."""
    assert normalize_phase_name("DISCOVERY") == "discovery"
    assert normalize_phase_name("spec_discovery") == "discovery"
    assert normalize_phase_name("DEV") == "dev"
    assert normalize_phase_name("coding") == "dev"
    assert normalize_phase_name("ci_loop") == "dev"
    assert normalize_phase_name("REVIEW_SECURITY") == "review_security"
    assert normalize_phase_name("security") == "review_security"
    assert normalize_phase_name("REVIEW_ARCH") == "review_architecture"
    assert normalize_phase_name("architecture") == "review_architecture"
    assert normalize_phase_name("qa_testing") == "review_qa"
    assert normalize_phase_name("QA") == "review_qa"


def test_skill_manager_init_defaults():
    """Verify default initialization parameters of SkillManager."""
    manager = SkillManager()
    assert manager.enabled is True
    assert manager.skills_dir.is_dir() or manager.skills_dir.name == "skills"
    assert manager.phases_config is None


def test_skill_manager_init_with_config():
    """Verify initializing SkillManager from SkillsConfig."""
    cfg = SkillsConfig(
        enabled=False,
        directory="./custom_skills",
        phases={"custom_phase": ["custom/skill.md"]},
    )
    manager = SkillManager(config=cfg)
    assert manager.enabled is False
    assert manager.skills_dir.name == "custom_skills"
    assert "custom_phase" in manager.phases_config
    assert manager.phases_config["custom_phase"] == ["custom/skill.md"]


# ============================================================================
# 2. SKILL LOADING TESTS
# ============================================================================

def test_load_standard_skills():
    """Verify all standard repository skills exist and load non-empty content."""
    manager = SkillManager()
    for phase, skills in DEFAULT_PHASE_SKILLS.items():
        for skill_path in skills:
            content = manager.load_skill(skill_path)
            assert content, f"Skill file '{skill_path}' for phase '{phase}' must load valid content"
            assert len(content) > 50


def test_load_skill_missing_file():
    """Verify missing skill files return empty string and do not raise errors."""
    manager = SkillManager()
    result = manager.load_skill("nonexistent/missing_skill.md")
    assert result == ""


def test_load_skill_empty_path():
    """Verify empty or blank skill paths return empty string."""
    manager = SkillManager()
    assert manager.load_skill("") == ""
    assert manager.load_skill("   ") == ""


def test_load_skill_custom_directory(tmp_path: Path):
    """Verify loading skills from a custom directory."""
    skill_file = tmp_path / "custom_category" / "my_custom_skill.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# My Custom Skill\nDo great work.", encoding="utf-8")

    manager = SkillManager(skills_dir=tmp_path)
    content = manager.load_skill("custom_category/my_custom_skill.md")
    assert "Do great work." in content


# ============================================================================
# 3. PHASE MAPPING & FORMATTING TESTS
# ============================================================================

def test_get_skills_for_phase_discovery():
    """Verify skill block generation for DISCOVERY phase."""
    manager = SkillManager()
    block = manager.get_skills_for_phase("DISCOVERY")
    assert "Active Domain Skills & Guidelines (DISCOVERY)" in block
    assert "Requirements Engineering" in block
    assert "Edge-Case & Boundary Analysis" in block


def test_get_skills_for_phase_dev():
    """Verify skill block generation for DEV phase containing all dev skills."""
    manager = SkillManager()
    block = manager.get_skills_for_phase("DEV")
    assert "Active Domain Skills & Guidelines (DEV)" in block
    assert "Clean Code & SOLID Principles" in block
    assert "Conventional Commits" in block
    assert "Test-Driven Development" in block
    assert "---" in block


def test_get_skills_for_phase_review_personas():
    """Verify skill block generation for review personas."""
    manager = SkillManager()

    sec_block = manager.get_skills_for_phase("REVIEW_SECURITY")
    assert "Application Security & OWASP Top 10 Audit" in sec_block
    assert "Injection Prevention" in sec_block

    arch_block = manager.get_skills_for_phase("REVIEW_ARCH")
    assert "Architecture & Code Quality Review" in arch_block
    assert "Code Smells & Anti-Patterns" in arch_block

    qa_block = manager.get_skills_for_phase("review_qa")
    assert "QA & Test Coverage Verification" in qa_block
    assert "Test Integrity & Meaningful Assertions" in qa_block


def test_get_skills_for_phase_dynamic_custom_skills(tmp_path: Path):
    """Verify dynamically appending custom skills to a phase."""
    custom_dir = tmp_path / "skills"
    custom_dir.mkdir()
    custom_skill = custom_dir / "project_rule.md"
    custom_skill.write_text("# Special Project Guideline\nNever use eval().", encoding="utf-8")

    manager = SkillManager(skills_dir=custom_dir)
    block = manager.get_skills_for_phase("DISCOVERY", custom_skills=["project_rule.md"])
    assert "Special Project Guideline" in block
    assert "Never use eval()." in block


def test_get_skills_for_phase_disabled():
    """Verify get_skills_for_phase returns empty string when disabled."""
    manager = SkillManager(enabled=False)
    assert manager.get_skills_for_phase("DEV") == ""
    assert manager.get_skills_for_phase("DISCOVERY") == ""


def test_get_skills_for_phase_unknown():
    """Verify unknown phase with no skills returns empty string."""
    manager = SkillManager()
    assert manager.get_skills_for_phase("unknown_phase_xyz") == ""


# ============================================================================
# 4. PROMPT INJECTION & PLACEHOLDER TESTS
# ============================================================================

def test_inject_skills_with_placeholder():
    """Verify inject_skills replaces {{PHASE_SKILLS}} placeholder cleanly."""
    manager = SkillManager()
    prompt_template = "You are an agent.\n\n## Skills\n{{PHASE_SKILLS}}\n\nFollow instructions."
    injected = manager.inject_skills(prompt_template, phase="discovery")
    assert "{{PHASE_SKILLS}}" not in injected
    assert "Requirements Engineering" in injected
    assert "You are an agent." in injected
    assert "Follow instructions." in injected


def test_inject_skills_placeholder_when_disabled():
    """Verify inject_skills cleanly removes placeholder when disabled."""
    manager = SkillManager(enabled=False)
    prompt_template = "Header\n\n{{PHASE_SKILLS}}\n\nFooter"
    injected = manager.inject_skills(prompt_template, phase="dev")
    assert "{{PHASE_SKILLS}}" not in injected
    assert "Header" in injected
    assert "Footer" in injected


def test_inject_skills_without_placeholder():
    """Verify inject_skills leaves prompt unchanged if placeholder is absent."""
    manager = SkillManager()
    raw = "Simple prompt without any placeholder."
    injected = manager.inject_skills(raw, phase="dev")
    assert injected == raw


# ============================================================================
# 5. INTEGRATION WITH PROMPT LOADERS
# ============================================================================

def test_discovery_prompt_loader_injects_skills():
    """Verify load_discovery_prompt has injected discovery skills."""
    prompt = load_discovery_prompt()
    assert "{{PHASE_SKILLS}}" not in prompt
    assert "Requirements Engineering" in prompt
    assert "Edge-Case & Boundary Analysis" in prompt


def test_developer_prompt_loader_injects_skills():
    """Verify load_developer_prompt has injected dev skills."""
    prompt = load_developer_prompt()
    assert "{{PHASE_SKILLS}}" not in prompt
    assert "Clean Code & SOLID Principles" in prompt
    assert "Test-Driven Development" in prompt
    assert "Conventional Commits" in prompt


def test_ci_healing_prompt_loader_injects_skills():
    """Verify load_ci_healing_prompt has injected dev skills."""
    prompt = load_ci_healing_prompt()
    assert "{{PHASE_SKILLS}}" not in prompt
    assert "Clean Code & SOLID Principles" in prompt


def test_reviewer_prompt_loaders_inject_skills():
    """Verify all reviewer persona prompts inject their corresponding skills."""
    sec_prompt = load_reviewer_prompt("security")
    assert "{{PHASE_SKILLS}}" not in sec_prompt
    assert "Application Security & OWASP Top 10 Audit" in sec_prompt

    arch_prompt = load_reviewer_prompt("architecture")
    assert "{{PHASE_SKILLS}}" not in arch_prompt
    assert "Architecture & Code Quality Review" in arch_prompt

    qa_prompt = load_reviewer_prompt("qa_testing")
    assert "{{PHASE_SKILLS}}" not in qa_prompt
    assert "QA & Test Coverage Verification" in qa_prompt


# ============================================================================
# 6. CONFIG.YAML INTEGRATION TEST
# ============================================================================

def test_config_yaml_has_skills_configured():
    """Verify config.yaml contains valid skills configuration section."""
    config = load_config("config.yaml")
    assert hasattr(config, "skills")
    assert config.skills.enabled is True
    assert config.skills.directory == "./skills"
    assert "discovery" in config.skills.phases
    assert "dev" in config.skills.phases
    assert "review_security" in config.skills.phases
    assert "review_architecture" in config.skills.phases
    assert "review_qa" in config.skills.phases
