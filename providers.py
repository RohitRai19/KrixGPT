"""
Model provider registry.

One place that knows which chat models exist, which provider serves them,
and how to build a LangChain chat model for any of them. Groq and xAI (Grok)
both expose OpenAI-compatible APIs, so they share ChatOpenAI with a custom
base_url; Gemini uses its own LangChain integration.

Only providers whose API key is present in the environment are offered to
the UI, so a missing key never produces a broken option in the dropdown.
"""

import os
import urllib.request
import urllib.error
from dotenv import load_dotenv

load_dotenv()

KEY_CHECK_TIMEOUT_SECONDS = 6
_key_status: dict[str, bool] = {}

PROVIDERS = {
    "xai": {
        "label": "Grok (xAI)",
        "env": ("XAI_API_KEY", "GROK_API_KEY"),
        "base_url": "https://api.x.ai/v1",
    },
    "groq": {
        "label": "Groq",
        "env": ("GROQ_API_KEY",),
        "base_url": "https://api.groq.com/openai/v1",
    },
    "gemini": {
        "label": "Gemini (Google)",
        "env": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    },
}

# Ordered: the first model whose provider has a key becomes the default.
MODELS = [
    # Groq first: it is the default provider whenever its key is configured.
    ("openai/gpt-oss-120b", "groq", "GPT-OSS 120B"),
    ("qwen/qwen3.8-27b", "groq", "Qwen 3.8 27B"),
    ("openai/gpt-oss-20b", "groq", "GPT-OSS 20B (fastest)"),
    ("grok-4-fast", "xai", "Grok 4 Fast"),
    ("grok-4", "xai", "Grok 4"),
    ("grok-3-mini", "xai", "Grok 3 Mini"),
    ("gemini-3.8-flash", "gemini", "Gemini 3.8 Flash"),
    ("gemini-3.7-flash", "gemini", "Gemini 3.7 Flash"),
    ("gemini-3.5-flash", "gemini", "Gemini 3.5 Flash"),
    ("gemini-3.5-flash-lite", "gemini", "Gemini 3.5 Flash Lite"),
    ("gemini-3.1-pro-preview", "gemini", "Gemini 3.1 Pro (preview)"),
    ("gemini-flash-latest", "gemini", "Gemini Flash (latest)"),
    ("gemini-pro-latest", "gemini", "Gemini Pro (latest)"),
]

# Cheapest sensible model per provider, used for background jobs like titles.
CHEAP_MODELS = {
    "xai": "grok-3-mini",
    "groq": "openai/gpt-oss-20b",
    "gemini": "gemini-3.5-flash-lite",
}

_MODEL_INDEX = {model_id: (provider, label) for model_id, provider, label in MODELS}


def provider_of(model_id: str) -> str | None:
    entry = _MODEL_INDEX.get(model_id)
    return entry[0] if entry else None


def api_key_for(provider: str) -> str | None:
    for name in PROVIDERS[provider]["env"]:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def _key_is_accepted(provider: str, key: str) -> bool:
    """
    One cheap request to the provider's model list to confirm the key works.
    A network problem counts as "accepted" so an offline dev box still boots;
    only an explicit 400/401/403 marks the key as rejected.
    """
    base_url = PROVIDERS[provider].get("base_url")
    if not base_url:
        return True  # Gemini: the SDK reports a bad key on first use

    req = urllib.request.Request(
        base_url.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {key}", "User-Agent": "KrixGPT"},
    )
    try:
        with urllib.request.urlopen(req, timeout=KEY_CHECK_TIMEOUT_SECONDS):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403):
            print(f"WARNING: {PROVIDERS[provider]['label']} rejected its API key "
                  f"(HTTP {exc.code}); that provider is hidden until the key is fixed.")
            return False
        return True
    except Exception:
        return True


def provider_available(provider: str) -> bool:
    """Key present and, for OpenAI-compatible providers, accepted by the API."""
    key = api_key_for(provider)
    if key is None:
        return False

    if provider not in _key_status:
        _key_status[provider] = _key_is_accepted(provider, key)

    return _key_status[provider]


def fallback_models(model_id: str) -> list[str]:
    """
    Models to try automatically if `model_id` fails (quota, outage, bad key):
    the first available model of every other configured provider.
    """
    primary = provider_of(model_id)
    per_provider: dict[str, list[str]] = {}
    for m in available_models():
        if m["id"] == model_id:
            continue
        per_provider.setdefault(m["provider"], []).append(m["id"])

    # Same provider first (handles a per-model daily quota or a flaky model),
    # then the other providers (handles an outage or a rejected key).
    result = list(per_provider.pop(primary, [])[:2]) if primary else []
    for models in per_provider.values():
        result.extend(models[:2])
    return result


def available_models() -> list[dict]:
    """Models whose provider has an API key configured, in display order."""
    return [
        {"id": model_id, "provider": provider, "label": label,
         "provider_label": PROVIDERS[provider]["label"]}
        for model_id, provider, label in MODELS
        if provider_available(provider)
    ]


def model_groups() -> list[dict]:
    """Available models grouped by provider, for an <optgroup> dropdown."""
    groups: dict[str, dict] = {}
    for m in available_models():
        groups.setdefault(m["provider"], {"label": m["provider_label"], "models": []})
        groups[m["provider"]]["models"].append(m)
    return list(groups.values())


def default_model() -> str:
    """DEFAULT_MODEL env override, else the first model with a configured key."""
    override = (os.getenv("DEFAULT_MODEL") or os.getenv("GEMINI_MODEL") or "").strip()
    if override and override in _MODEL_INDEX and provider_available(_MODEL_INDEX[override][0]):
        return override

    models = available_models()
    if models:
        return models[0]["id"]

    # No keys at all: keep the app bootable; the first call will explain why.
    return MODELS[0][0]


def cheap_model() -> str:
    """A cheap model from the same provider as the default, for titles etc."""
    provider = provider_of(default_model()) or "gemini"
    if provider_available(provider):
        return CHEAP_MODELS[provider]
    for p, model_id in CHEAP_MODELS.items():
        if provider_available(p):
            return model_id
    return CHEAP_MODELS["gemini"]


def normalize_model_name(model_name: str | None) -> str:
    """Return a usable model id: the requested one if known and configured, else the default."""
    if not model_name:
        return default_model()

    model_name = model_name.strip()
    provider = provider_of(model_name)

    if provider is None or not provider_available(provider):
        return default_model()

    return model_name


def create_chat_model(
    model_id: str,
    *,
    temperature: float = 0.3,
    streaming: bool = False,
    max_retries: int = 2,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
):
    """Build a LangChain chat model for any registered model id."""
    model_id = normalize_model_name(model_id)
    provider = provider_of(model_id)

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        kwargs = dict(
            model=model_id,
            temperature=temperature,
            streaming=streaming,
            max_retries=max_retries,
        )
        if max_tokens:
            kwargs["max_output_tokens"] = max_tokens
        return ChatGoogleGenerativeAI(**kwargs)

    from langchain_openai import ChatOpenAI

    kwargs = dict(
        model=model_id,
        api_key=api_key_for(provider),
        base_url=PROVIDERS[provider]["base_url"],
        temperature=temperature,
        streaming=streaming,
        max_retries=max_retries,
    )
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    if "gpt-oss" in model_id and reasoning_effort:
        # Reasoning models spend tokens thinking before answering; keep short
        # background jobs (titles) cheap and fast.
        kwargs["reasoning_effort"] = reasoning_effort
    return ChatOpenAI(**kwargs)
