from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import OperationalError

Path("data").mkdir(exist_ok=True)

DATABASE_URL = "sqlite:///data/chatbot_memory.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(String, unique=True, index=True)
    user_id = Column(String, index=True, nullable=True)
    title = Column(String, default="New Chat")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(String, index=True)
    role = Column(String)
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class LongTermMemory(Base):
    __tablename__ = "long_term_memory"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(String, index=True)
    memory = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate()


def _migrate():
    """Add columns introduced after the first release to existing SQLite files."""
    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("conversations")}

    if "user_id" not in columns:
        with engine.begin() as conn:
            try:
                conn.execute(text("ALTER TABLE conversations ADD COLUMN user_id VARCHAR"))
            except OperationalError as exc:
                # Another process (e.g. uvicorn --reload) migrated first.
                if "duplicate column" not in str(exc).lower():
                    raise
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_user_id ON conversations (user_id)"))


def create_or_update_conversation(
    thread_id: str,
    first_message: str | None = None,
    user_id: str | None = None
):
    db = SessionLocal()

    try:
        conversation = (
            db.query(Conversation)
            .filter(Conversation.thread_id == thread_id)
            .first()
        )

        if conversation and user_id and conversation.user_id and conversation.user_id != user_id:
            raise PermissionError("This conversation belongs to another user.")

        if not conversation:
            title = "New Chat"

            if first_message:
                title = first_message.strip()[:40]
                if len(first_message.strip()) > 40:
                    title += "..."

            conversation = Conversation(
                thread_id=thread_id,
                user_id=user_id,
                title=title,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )

            db.add(conversation)

        else:
            conversation.updated_at = datetime.utcnow()
            if user_id and not conversation.user_id:
                conversation.user_id = user_id

        db.commit()

    finally:
        db.close()


def list_conversations(user_id: str | None = None):
    db = SessionLocal()

    try:
        query = db.query(Conversation)

        if user_id is not None:
            query = query.filter(Conversation.user_id == user_id)

        return query.order_by(Conversation.updated_at.desc()).all()

    finally:
        db.close()


def list_conversations_by_ids(thread_ids: list[str]):
    """Guest sidebar: only unowned conversations whose ids the browser remembers."""
    if not thread_ids:
        return []

    db = SessionLocal()

    try:
        return (
            db.query(Conversation)
            .filter(Conversation.thread_id.in_(thread_ids))
            .filter(Conversation.user_id.is_(None))
            .order_by(Conversation.updated_at.desc())
            .all()
        )

    finally:
        db.close()


def claim_conversations(thread_ids: list[str], user_id: str) -> int:
    """Attach guest (unowned) conversations to a user who just signed in."""
    if not thread_ids or not user_id:
        return 0

    db = SessionLocal()

    try:
        updated = (
            db.query(Conversation)
            .filter(Conversation.thread_id.in_(thread_ids))
            .filter(Conversation.user_id.is_(None))
            .update({Conversation.user_id: user_id}, synchronize_session=False)
        )
        db.commit()
        return updated

    finally:
        db.close()


def save_chat_message(thread_id: str, role: str, content: str):
    db = SessionLocal()

    try:
        msg = ChatMessage(
            thread_id=thread_id,
            role=role,
            content=content,
            created_at=datetime.utcnow()
        )

        db.add(msg)

        conversation = (
            db.query(Conversation)
            .filter(Conversation.thread_id == thread_id)
            .first()
        )

        if conversation:
            conversation.updated_at = datetime.utcnow()

        db.commit()

    finally:
        db.close()


def conversation_owner(thread_id: str) -> str | None:
    """Return the owning user_id, or None if the conversation is missing/unowned."""
    db = SessionLocal()

    try:
        conversation = (
            db.query(Conversation)
            .filter(Conversation.thread_id == thread_id)
            .first()
        )
        return conversation.user_id if conversation else None

    finally:
        db.close()


def get_chat_history(thread_id: str):
    db = SessionLocal()

    try:
        return (
            db.query(ChatMessage)
            .filter(ChatMessage.thread_id == thread_id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )

    finally:
        db.close()


def save_memory(thread_id: str, memory: str):
    db = SessionLocal()

    try:
        item = LongTermMemory(
            thread_id=thread_id,
            memory=memory,
            created_at=datetime.utcnow()
        )

        db.add(item)
        db.commit()

        return "Memory saved successfully."

    finally:
        db.close()


def search_memory(thread_id: str, query: str):
    db = SessionLocal()

    try:
        memories = (
            db.query(LongTermMemory)
            .filter(LongTermMemory.thread_id == thread_id)
            .order_by(LongTermMemory.created_at.desc())
            .limit(20)
            .all()
        )

        if not memories:
            return "No saved memory found."

        return "\n".join([f"- {m.memory}" for m in memories])

    finally:
        db.close()