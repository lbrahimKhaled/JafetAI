import os

from google.adk.models.lite_llm import LiteLlm

# deepseek v4 flash is the production default; override with MODEL env var
# (e.g. MODEL=openrouter/... for free dev runs)
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"


def get_model():
    model = os.environ.get("MODEL", DEFAULT_MODEL)
    # litellm wants NVIDIA_NIM_API_KEY, .env has NVIDIA_API_KEY
    if model.startswith("nvidia_nim/") and "NVIDIA_NIM_API_KEY" not in os.environ:
        os.environ["NVIDIA_NIM_API_KEY"] = os.environ.get("NVIDIA_API_KEY", "")
    return LiteLlm(model=model)
