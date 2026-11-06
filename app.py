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
    conversation_owner)

from auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    verify_firebase_id_token,
    create_session_token,
    get_current_user,
    get_optional_user)

from rag import add_document_to_rag
from tools import set_current_thread_id


app = FastAPI()

templates = Jinja2Templates(directory="templates")

Path("uploads").mkdir(exist_ok=True)
Path("data").mkdir(exist_ok=True)


init_db()


FIREBASE_WEB_CONFIG = {
    "apiKey": os.getenv("FIREBASE_API_KEY", "AIzaSyBUV_BZju0ffdtMJ79-hSiOsjLb0rzVRSA"),
    "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN", "raiyronai.firebaseapp.com"),
    "projectId": os.getenv("FIREBASE_PROJECT_ID", "raiyronai"),
    "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET", "raiyronai.firebasestorage.app"),
    "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID", "760542218753"),
    "appId": os.getenv("FIREBASE_APP_ID", "1:760542218753:web:ebfb23af2de8ace3b0ce00"),
    "measurementId": os.getenv("FIREBASE_MEASUREMENT_ID", "G-XX45XZ6DXE"),
}


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





if __name__ == "__main__":
   
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8080,
        reload=True
    )