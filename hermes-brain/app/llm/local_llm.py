from __future__ import annotations

from pathlib import Path
import subprocess
import logging
import shutil

from .prompt_templates import build_prompt

logger = logging.getLogger("app.llm.local_llm")


class LocalLLM:
    def __init__(self, model_path: Path, llama_bin: Path, timeout_seconds: int = 60) -> None:
        self.model_path = model_path
        self.llama_bin = llama_bin
        self.timeout_seconds = timeout_seconds

    def _resolve_llama_bin(self) -> str | None:
        if self.llama_bin.exists():
            return str(self.llama_bin)
        resolved = shutil.which(str(self.llama_bin))
        return resolved

    def generate(self, question: str, context: str) -> str:
        if not self.model_path.exists():
            return (
                "LLM disabled: no local model installed. Answer is based on retrieved knowledge only.\n\n"
                f"Retrieved context:\n{context or '(no local context)'}"
            )

        llama_path = self._resolve_llama_bin()
        if not llama_path:
            return (
                "llama.cpp binary not found. "
                "Set HERMES_LLAMA_BIN to the llama.cpp executable path or ensure it is on PATH.\n"
                "Returning retrieved context only.\n\n"
                f"Retrieved context:\n{context or '(no local context)'}"
            )

        prompt = build_prompt(question, context)
        cmd = [
            llama_path,
            "-m",
            str(self.model_path),
            "-p",
            prompt,
            "--ctx-size",
            "2048",
            "--threads",
            "4",
        ]
        try:
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            logger.error("Failed to run llama.cpp: %s", exc)
            return (
                "Local LLM execution failed. Returning retrieved context only.\n\n"
                f"Retrieved context:\n{context or '(no local context)'}"
            )

        output = (result.stdout or "").strip()
        if not output:
            output = "Local LLM returned no output."
        return output
