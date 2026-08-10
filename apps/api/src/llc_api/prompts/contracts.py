from pathlib import Path

PROMPT_ROOT = Path(__file__).resolve().parent
TEACHER_CONTRACT_VERSION = "gemma-teacher.v1"


def teacher_prompt(mode_prompt: str) -> str:
    """Compose the versioned teacher contract with one small mode appendix."""
    contract = (PROMPT_ROOT / "gemma_teacher_v1.md").read_text(encoding="utf-8").strip()
    mode = (PROMPT_ROOT / mode_prompt).read_text(encoding="utf-8").strip()
    return f"{contract}\n\n# Instrucciones específicas del modo\n\n{mode}\n"
