from dotenv import load_dotenv
import os
import certifi

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

import json
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, UploadFile, File, Form, Depends, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    AIMessageChunk,
    ToolMessage
)

from agent import get_agent
from database import (
    init_db,
    save_chat_message,
    get_chat_history,
    create_or_update_conversation,
    list_conversations,
    list_conversations_by_ids,
    claim_conversations,
    claim_memories,
    conversation_owner,
    update_conversation_title,
    count_messages)

from titles import generate_title
from providers import model_groups, default_model

from auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    verify_firebase_id_token,
    create_session_token,
    get_current_user,
    get_optional_user)

from rag import add_document_to_rag
from tools import set_current_thread_id, set_current_owner_id


app = FastAPI()

templates = Jinja2Templates(directory="templates")

Path("uploads").mkdir(exist_ok=True)
Path("data").mkdir(exist_ok=True)


init_db()


# Firebase web config: public identifiers shipped to every browser, but kept in
# the environment so they can be rotated without a code change. If the API key
# is missing, the UI shows "Sign-in is not configured" and guest chat still works.
_FIREBASE_ENV = {
    "apiKey": "FIREBASE_API_KEY",
    "authDomain": "FIREBASE_AUTH_DOMAIN",
    "projectId": "FIREBASE_PROJECT_ID",
    "storageBucket": "FIREBASE_STORAGE_BUCKET",
    "messagingSenderId": "FIREBASE_MESSAGING_SENDER_ID",
    "appId": "FIREBASE_APP_ID",
    "measurementId": "FIREBASE_MEASUREMENT_ID",
}
FIREBASE_WEB_CONFIG = {k: v for k, v in ((k, os.getenv(env, "").strip()) for k, env in _FIREBASE_ENV.items()) if v}

if not FIREBASE_WEB_CONFIG.get("apiKey"):
    print("WARNING: FIREBASE_API_KEY not set; Google sign-in is disabled (guest mode only).")


def _ensure_owner(thread_id: str, user: dict | None):
    """
    Reject access to a conversation owned by a different user.
    Guests (user is None) may only touch unowned conversations.
    """
    owner = conversation_owner(thread_id)
    if owner is not None and (user is None or owner != user["uid"]):
        raise HTTPException(status_code=403, detail="This conversation belongs to another user.")


def _uid(user: dict | None) -> str | None:
    return user["uid"] if user else None


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "firebase_config": json.dumps(FIREBASE_WEB_CONFIG),
            "current_user": get_optional_user(request),
            "model_groups": model_groups(),
            "default_model": default_model(),
        }
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.post("/auth/login")
async def auth_login(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body."}, status_code=400)

    id_token = data.get("idToken", "")
    if not id_token:
        return JSONResponse({"error": "idToken is required."}, status_code=400)

    user = verify_firebase_id_token(id_token)

    # Conversations started as a guest in this browser become the user's.
    guest_thread_ids = data.get("guestThreadIds") or []
    if isinstance(guest_thread_ids, list):
        claim_conversations([str(t) for t in guest_thread_ids][:200], user["uid"])

    # Memories saved while browsing as a guest follow the user into their account.
    guest_id = str(data.get("guestId") or "").strip()[:64]
    if guest_id:
        claim_memories(f"guest:{guest_id}", user["uid"])

    response = JSONResponse({"user": user})
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_token(user),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


@app.post("/auth/logout")
async def auth_logout():
    response = JSONResponse({"success": True})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@app.get("/auth/me")
async def auth_me(request: Request):
    user = get_optional_user(request)
    if not user:
        return JSONResponse({"user": None}, status_code=401)
    return {"user": user}



@app.get("/conversations")
async def conversations(
    thread_ids: str = "",
    user: dict | None = Depends(get_optional_user)
):
    if user:
        items = list_conversations(user_id=user["uid"])
    else:
        ids = [t for t in thread_ids.split(",") if t][:200]
        items = list_conversations_by_ids(ids)

    return {
        "conversations": [
            {
                "thread_id": item.thread_id,
                "title": item.title,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat()
            }
            for item in items
        ]
    }



@app.get("/history/{thread_id}")
async def history(thread_id: str, user: dict | None = Depends(get_optional_user)):
    _ensure_owner(thread_id, user)
    messages = get_chat_history(thread_id)

    return {
        "messages": [
            {
                "role": msg.role,
                "content": msg.content
            }
            for msg in messages
        ]
    }




@app.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    thread_id: str = Form(...),
    user: dict | None = Depends(get_optional_user)
):
    _ensure_owner(thread_id, user)

    try:
        allowed_extensions = [".pdf", ".docx", ".txt", ".md", ".py", ".csv"]

        filename = file.filename or "uploaded_file"
        suffix = Path(filename).suffix.lower()

        if suffix not in allowed_extensions:
            return JSONResponse(
                {
                    "success": False,
                    "message": "Unsupported file type. Upload PDF, DOCX, TXT, MD, PY, or CSV."
                },
                status_code=400
            )

        file_id = str(uuid.uuid4())
        safe_filename = filename.replace(" ", "_")
        file_path = f"uploads/{file_id}_{safe_filename}"

        with open(file_path, "wb") as f:
            f.write(await file.read())

        create_or_update_conversation(thread_id, "Uploaded document", user_id=_uid(user))

        result = add_document_to_rag(
            file_path=file_path,
            thread_id=thread_id
        )

        return JSONResponse({
            "success": True,
            "message": f"Uploaded {result['filename']} and created {result['chunks']} chunks."
        })

    except Exception as e:
        return JSONResponse(
            {
                "success": False,
                "message": str(e)
            },
            status_code=500
        )



def sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def should_stream_chunk(chunk, metadata) -> bool:
    """
    This prevents raw tool/search/RAG JSON from appearing in the frontend.

    We only stream normal AI text chunks.
    We do NOT stream:
    - ToolMessage
    - messages from tool nodes
    - tool call chunks
    - raw tool outputs
    """

    metadata = metadata or {}

    node_name = str(metadata.get("langgraph_node", "")).lower()

    if "tool" in node_name:
        return False

    if isinstance(chunk, ToolMessage):
        return False

    if not isinstance(chunk, (AIMessage, AIMessageChunk)):
        return False

    if getattr(chunk, "tool_calls", None):
        return False

    if getattr(chunk, "invalid_tool_calls", None):
        return False

    additional_kwargs = getattr(chunk, "additional_kwargs", {}) or {}

    if additional_kwargs.get("tool_calls"):
        return False

    return True


def extract_text_from_chunk(chunk) -> str:
    content = getattr(chunk, "content", "")

    if not content:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts = []

        for item in content:
            if isinstance(item, str):
                text_parts.append(item)

            elif isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
                elif isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    text_parts.append(item["content"])

        return "".join(text_parts)

    return ""



@app.post("/chat/stream")
async def chat_stream(request: Request, user: dict | None = Depends(get_optional_user)):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON body."},
            status_code=400
        )

    user_message = data.get("message", "")
    thread_id = data.get("thread_id", "default")
    selected_model = data.get("model")

    if not user_message.strip():
        return JSONResponse(
            {"error": "Message is required."},
            status_code=400
        )

    _ensure_owner(thread_id, user)

    agent = get_agent(selected_model)

    create_or_update_conversation(thread_id, user_message, user_id=_uid(user))
    save_chat_message(thread_id, "user", user_message)

    set_current_thread_id(thread_id)

    guest_id = str(data.get("guest_id") or "").strip()[:64]
    owner_id = user["uid"] if user else (f"guest:{guest_id}" if guest_id else thread_id)
    set_current_owner_id(owner_id)

    # Title the thread after its first full exchange, like ChatGPT does.
    is_first_turn = count_messages(thread_id, role="assistant") == 0

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    def event_generator():
        final_answer = ""

        try:
            inputs = {
                "messages": [
                    HumanMessage(content=user_message)
                ]
            }

            for chunk, metadata in agent.stream(
                inputs,
                config=config,
                stream_mode="messages"
            ):
                if not should_stream_chunk(chunk, metadata):
                    continue

                token = extract_text_from_chunk(chunk)

                if token:
                    final_answer += token
                    yield sse_data({"token": token})

            if final_answer.strip():
                save_chat_message(thread_id, "assistant", final_answer)

                if is_first_turn:
                    title = generate_title(user_message, final_answer)
                    update_conversation_title(thread_id, title)
                    yield sse_data({"title": title})

            yield sse_data({"done": True})

        except Exception as e:
            yield sse_data({"error": str(e)})
            yield sse_data({"done": True})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )





# ---------------------------------------------------------------------------
# Hugging Face Spaces (free "Gradio" SDK) runs app.py and expects a Gradio app
# on port 7860. Gradio is built on FastAPI, so we mount a tiny Gradio page at
# /gradio and keep serving the real UI at "/". Locally this is harmless.
# ---------------------------------------------------------------------------
try:
    import gradio as gr

    # Hugging Face's free "ZeroGPU" tier only accepts apps that declare at least
    # one @spaces.GPU function. KrixGPT needs no GPU (all models are API calls),
    # so this placeholder satisfies the check and is never invoked. Harmless
    # elsewhere: the import fails outside Spaces and the block is skipped.
    try:
        import spaces

        @spaces.GPU(duration=1)
        def _zero_gpu_placeholder() -> str:
            return "KrixGPT runs its models via provider APIs; no GPU work here."

        # `spaces` normally reports the decorated functions to ZeroGPU from a
        # patched gr.Blocks.launch(). We serve through uvicorn and never call
        # launch(), so send that startup report ourselves.
        _zero_startup = getattr(getattr(spaces, "zero", None), "startup", None)
        if callable(_zero_startup):
            _zero_startup()
            print("ZeroGPU startup report sent (placeholder GPU function registered).")
    except Exception as _spaces_exc:
        print(f"ZeroGPU setup skipped: {type(_spaces_exc).__name__}: {_spaces_exc}")

    with gr.Blocks(title="KrixGPT") as _gradio_demo:
        gr.Markdown("## KrixGPT - the full chat UI is served at the site root: [Open KrixGPT](/)")

    app = gr.mount_gradio_app(app, _gradio_demo, path="/gradio")
except ImportError:
    pass


if __name__ == "__main__":
    on_spaces = bool(os.getenv("SPACE_ID"))
    port = int(os.getenv("PORT") or os.getenv("GRADIO_SERVER_PORT") or (7860 if on_spaces else 8080))

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port,
        reload=not on_spaces,
    )
