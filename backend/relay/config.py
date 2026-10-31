import os
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Config:
    port: int = int(_env("RELAY_PORT", "8100"))
    backend_url: str = _env("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
    internal_token: str = _env("INTERNAL_TOKEN")
    dashboard_url: str = _env("DASHBOARD_URL", "http://localhost:5173").rstrip("/")
    anthropic_key: str = _env("ANTHROPIC_API_KEY")
    triage_model: str = _env("TRIAGE_MODEL", "claude-haiku-4-5")
    coach_model: str = _env("COACH_MODEL", "claude-sonnet-5-5")
    fixer_model: str = _env("FIXER_MODEL", "claude-sonnet-5-5")
    verifier_model: str = _env("VERIFIER_MODEL", "claude-haiku-4-5")
    supervisor_email: str = _env("SUPERVISOR_EMAIL")  # unset: emails go to the outbox file only
    prompts_version: str = _env("PROMPTS_VERSION", "v1")
    verify_wait_s: float = float(_env("VERIFY_WAIT_S", "90"))
    data_dir: Path = field(default_factory=lambda: DATA)

    def prompt(self, name: str) -> str:
        return (HERE / "prompts" / self.prompts_version / f"{name}.md").read_text().strip()


CONFIG = Config()
