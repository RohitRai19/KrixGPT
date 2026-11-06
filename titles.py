"""
Conversation title generation.

After the first exchange in a thread we ask a small, cheap model for a short
title (the way ChatGPT does). If the model call fails for any reason, such as
quota or an outage, we fall back to a cleaned-up version of the user's first
message so the sidebar never shows a raw prompt.
"""

import os
import re

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage

from providers import cheap_model, create_chat_model

load_dotenv()

TITLE_MODEL = os.getenv("TITLE_MODEL") or cheap_model()
TITLES_ENABLED = os.getenv("GENERATE_TITLES", "true").lower() not in {"0", "false", "no"}

MAX_TITLE_CHARS = 48
MAX_TITLE_WORDS = 6

_TITLE_PROMPT = (
    "You write short titles for chat conversations. "
    "Given the user's first message and the assistant's reply, respond with a "
    "concise title of 3 to 6 words in Title Case that captures the topic. "
    "No quotes, no trailing punctuation, no emojis, no prefix like 'Title:'."
)

_FILLER = re.compile(
    r"^(hi|hii+|hello|hey|yo|please|pls|can you|could you|would you|"
    r"i want to|i need to|i want|i need|tell me|help me|explain|about)\b[\s,:-]*",
    re.IGNORECASE,
)

_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        _llm = create_chat_model(
            TITLE_MODEL,
            temperature=0.2,
            max_tokens=24,
            max_retries=0,
        )
    return _llm


def _clean(raw: str) -> str:
    title = raw.strip().splitlines()[0] if raw and raw.strip() else ""
    title = re.sub(r"^(title\s*[:\-]\s*)", "", title, flags=re.IGNORECASE)
    title = title.strip(" \"'`*_.!?:;-")
    title = re.sub(r"\s+", " ", title)

    words = title.split(" ")
    if len(words) > MAX_TITLE_WORDS:
        title = " ".join(words[:MAX_TITLE_WORDS])

    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:-")

    return title


def heuristic_title(user_message: str) -> str:
    """Title without a model call: drop greetings and filler, keep the gist."""
    text = re.sub(r"\s+", " ", (user_message or "").strip())

    for _ in range(3):
        stripped = _FILLER.sub("", text)
        if stripped == text:
            break
        text = stripped

    text = text.strip(" \"'`.!?:;,-")

    if not text:
        return "New Chat"

    words = text.split(" ")[:MAX_TITLE_WORDS]
    title = " ".join(words)

    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS].rsplit(" ", 1)[0]

    # Capitalise the first letter only; leave acronyms and code alone.
    return title[0].upper() + title[1:] if title else "New Chat"


def generate_title(user_message: str, assistant_reply: str) -> str:
    """Best-effort short title for a conversation."""
    fallback = heuristic_title(user_message)

    if not TITLES_ENABLED:
        return fallback

    try:
        llm = _get_llm()
        response = llm.invoke(
            [
                SystemMessage(content=_TITLE_PROMPT),
                HumanMessage(
                    content=(
                        f"User message:\n{(user_message or '')[:1200]}\n\n"
                        f"Assistant reply:\n{(assistant_reply or '')[:1200]}"
                    )
                ),
            ]
        )
        content = response.content
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        title = _clean(str(content))
        return title if len(title) >= 3 else fallback

    except Exception as exc:  # quota, network, model errors: never break the chat
        print(f"Title generation fell back to heuristic: {type(exc).__name__}")
        return fallback
