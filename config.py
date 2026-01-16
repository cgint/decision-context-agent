import os
import dspy
from pathlib import Path
from contextlib import contextmanager
from typing import Literal

TYPE_REASONING_EFFORT = Literal["low", "medium", "high", "disable"] | None

DEFAULT_MODEL: str = "gemini-2.5-flash"
DEFAULT_REASONING_EFFORT: TYPE_REASONING_EFFORT = "disable"
DEFAULT_TEMPERATURE: float = 0.3
# DEFAULT_MODEL = "gemini-3-flash-preview"
# DEFAULT_REASONING_EFFORT = None
# DEFAULT_TEMPERATURE = 1.0


class DSPyGeminiConfig:
    @classmethod
    def configure(
        cls,
        model_name: str = DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        reasoning_effort: TYPE_REASONING_EFFORT = DEFAULT_REASONING_EFFORT
    ) -> None:
        """
        Configure DSPy global defaults.

        Important: `dspy.settings.configure()` must be called only once and from the same
        thread that initially configured it. For concurrent execution, prefer
        `DSPyGeminiConfig.context(...)` to set `lm` (and adapter) per thread without
        reconfiguring global settings.
        """
        lm = cls._get_lm(model_name, temperature, reasoning_effort)
        dspy.settings.configure(lm=lm, adapter=dspy.JSONAdapter())
        dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)

    @classmethod
    def create_lm(
        cls,
        model_name: str = DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        reasoning_effort: TYPE_REASONING_EFFORT = DEFAULT_REASONING_EFFORT,
    ) -> dspy.LM:
        return cls._get_lm(model_name, temperature, reasoning_effort)

    @classmethod
    @contextmanager
    def context(
        cls,
        model_name: str = DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        reasoning_effort: TYPE_REASONING_EFFORT = DEFAULT_REASONING_EFFORT,
        *,
        track_usage: bool = False,
    ):
        lm = cls._get_lm(model_name, temperature, reasoning_effort)
        with dspy.context(lm=lm, adapter=dspy.JSONAdapter(), track_usage=track_usage):
            yield

    @classmethod
    def _get_lm(
        cls,
        model_name: str,
        temperature: float,
        reasoning_effort: TYPE_REASONING_EFFORT
    ) -> dspy.LM:
        prefix = cls._get_model_access_prefix_or_fail(model_name)
        return dspy.LM(
            model=f"{prefix}{model_name}",
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )

    @classmethod
    def _get_model_access_prefix_or_fail(cls, model_name: str) -> str:
        if "/" in model_name:
            return ""

        has_vertex = os.getenv("VERTEXAI_PROJECT") and os.getenv("VERTEXAI_LOCATION")
        has_gemini = os.getenv("GEMINI_API_KEY")

        if has_gemini:
            return "gemini/"
        elif has_vertex:
            return "vertex_ai/"
        else:
            raise ValueError(
                "Either (VERTEXAI_PROJECT + VERTEXAI_LOCATION) or GEMINI_API_KEY must be set."
            )


class PathConfig:
    SESSIONS_DIR = Path(os.getenv("SESSIONS_DIR", "sessions"))
    DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
    PATTERNS_DIR = DATA_DIR / "patterns"

    @classmethod
    def get_source_data_dir(cls, source: str) -> Path:
        return cls.DATA_DIR / source

    @classmethod
    def get_session_dir(cls, source: str, session_id: str) -> Path:
        return cls.get_source_data_dir(source) / session_id

    @classmethod
    def get_events_path(cls, source: str, session_id: str) -> Path:
        return cls.get_session_dir(source, session_id) / "events.json"

    @classmethod
    def get_traces_path(cls, source: str, session_id: str) -> Path:
        return (
            cls.get_session_dir(source, session_id) / "reasoning_traces_automated.json"
        )
