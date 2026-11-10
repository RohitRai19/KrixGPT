import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
import certifi

load_dotenv()

os.environ["SSL_CERT_FILE"]=certifi.where()
os.environ["REQUESTS_CA_BUNDLE"]=certifi.where()

from langchain_core.messages import SystemMessage
from langgraph.graph import StateGraph,START,MessagesState
from langgraph.prebuilt import  ToolNode,tools_condition
from langgraph.checkpoint.sqlite import SqliteSaver
from tools import tools
from providers import normalize_model_name, create_chat_model, default_model, fallback_models

Path("data").mkdir(exist_ok=True)

SYSTEM_PROMPT = """
You are a helpful Agentic AI assistant named KrixGPT similar to ChatGPT.

You can:
1. Answer normal questions.
2. Use tools when needed.
3. Search uploaded documents using the RAG tool.
4. Search the web for latest/current information using Tavily Search.
5. Remember important user information using the memory tool.
6. Recall memory when useful.
7. Use calculator for math.
8. Get live weather for any place using get_weather.
9. Get live stock prices and market stats using get_stock_price.

Rules:
- If the user asks about latest news, current events, recent updates, today's information, current prices, current people, current versions, new releases, or anything time-sensitive, use Tavily Search.
- If the user asks about an uploaded document, use search_uploaded_documents.
- If the user asks you to remember something, use remember_this.
- If the user asks about previous preferences or saved facts, use recall_memory.
- Use calculator for math questions.
- If the user asks about weather, temperature, rain or forecast for a place, use get_weather (never guess the weather).
- If the user asks about a share price, stock, ticker, market cap or how a company's stock is doing, use get_stock_price. Convert company names to tickers (e.g. Apple -> AAPL, Reliance -> RELIANCE.NS, Nifty 50 -> ^NSEI). Add a one-line note that prices may be delayed.
- When using web search, summarize clearly and mention that the answer is based on web search results.
- Be clear, helpful, and concise.
"""

def build_agent(model_name: str):
    """
    Build one LangGraph agent for a selected model (Grok, Groq or Gemini).
    """

    selected_model = normalize_model_name(model_name)

    llm = create_chat_model(
        selected_model,
        temperature=0.3,
        streaming=True,
        max_retries=1,
    )

    llm_with_tools = llm.bind_tools(tools)

    # If the chosen provider fails (daily quota, outage, rejected key), retry
    # the same request on another configured provider instead of surfacing
    # the error. The next message goes back to the user's chosen model.
    backups = [
        create_chat_model(m, temperature=0.3, streaming=True, max_retries=1).bind_tools(tools)
        for m in fallback_models(selected_model)
    ]
    if backups:
        llm_with_tools = llm_with_tools.with_fallbacks(backups)

    def chatbot_node(state: MessagesState):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]

        response = llm_with_tools.invoke(messages)

        return {
            "messages": [response]
        }

    tool_node = ToolNode(tools)

    workflow = StateGraph(MessagesState)

    workflow.add_node("chatbot", chatbot_node)
    workflow.add_node("tools", tool_node)

    workflow.add_edge(START, "chatbot")
    workflow.add_conditional_edges("chatbot", tools_condition)
    workflow.add_edge("tools", "chatbot")

    conn = sqlite3.connect(
        "data/langgraph_checkpoints.sqlite",
        check_same_thread=False
    )

    checkpointer = SqliteSaver(conn)

    return workflow.compile(checkpointer=checkpointer)


CHECKPOINT_DB = "data/langgraph_checkpoints.sqlite"


def delete_thread_checkpoints(thread_id: str) -> None:
    """Forget the LangGraph state of a deleted conversation."""
    try:
        conn = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
        try:
            SqliteSaver(conn).delete_thread(thread_id)
        finally:
            conn.close()
    except Exception as exc:
        print(f"Checkpoint cleanup skipped for {thread_id}: {type(exc).__name__}")


_AGENT_CACHE = {}


def get_agent(model_name: str | None = None):
    """
    Return cached LangGraph agent for selected model.
    If not created yet, create it once and reuse it.
    """

    selected_model = normalize_model_name(model_name)

    if selected_model not in _AGENT_CACHE:
        _AGENT_CACHE[selected_model] = build_agent(selected_model)

    return _AGENT_CACHE[selected_model]