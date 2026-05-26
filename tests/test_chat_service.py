from __future__ import annotations

import asyncio

import pytest

from pydantic_ai import AgentRunResultEvent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from minicc.core.chat_events import Done, Error, TextDelta, ToolFinished, ToolStarted
from minicc.core.chat_service import ChatService
from minicc.core.config import load_config
from minicc.core.models import Config, MiniCCDeps, Provider, UserCancelledError
from minicc.core.persistence import MessageStore


def _make_deps() -> MiniCCDeps:
    return MiniCCDeps(config=Config(provider=Provider.ANTHROPIC, model="test-model", api_key="test-key"), cwd="/tmp/test", fs=None)


def _make_store(tmp_path):
    return MessageStore(path=tmp_path / "history.json")


class _FakeUsage:
    def __init__(self):
        self.request_tokens = 10
        self.response_tokens = 5


class _FakeResult:
    def __init__(self, output="", messages=None, usage=None):
        self._output = output
        self._messages = messages or []
        self._usage = usage or _FakeUsage()

    @property
    def output(self):
        return self._output

    def all_messages(self):
        return self._messages

    def usage(self):
        return self._usage


class FakeAgent:
    """Produces pydantic-ai stream events from a predefined list."""

    def __init__(self, events):
        self._events = events
        self._last_prompt = None
        self._last_deps = None
        self._last_history = None

    async def run_stream_events(self, prompt, deps=None, message_history=None):
        self._last_prompt = prompt
        self._last_deps = deps
        self._last_history = message_history
        for ev in self._events:
            yield ev


@pytest.mark.asyncio
async def test_send_message_yields_text_delta(tmp_path):
    events = [
        PartStartEvent(index=0, part=TextPart(content="Hello")),
        PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=" World")),
        AgentRunResultEvent(result=_FakeResult(output="Hello World")),
    ]
    agent = FakeAgent(events)
    deps = _make_deps()
    store = _make_store(tmp_path)
    svc = ChatService(agent=agent, deps=deps, store=store)

    results = []
    async for ev in svc.send_message("hi"):
        results.append(ev)

    assert len(results) == 3
    assert isinstance(results[0], TextDelta) and results[0].content == "Hello"
    assert isinstance(results[1], TextDelta) and results[1].content == "Hello World"
    assert isinstance(results[2], Done)


@pytest.mark.asyncio
async def test_send_message_passes_history(tmp_path):
    events = [
        AgentRunResultEvent(result=_FakeResult(output="ok")),
    ]
    agent = FakeAgent(events)
    deps = _make_deps()
    store = _make_store(tmp_path)
    svc = ChatService(agent=agent, deps=deps, store=store)

    async for _ in svc.send_message("hi"):
        pass

    assert agent._last_prompt == "hi"
    assert agent._last_history == []


@pytest.mark.asyncio
async def test_send_message_yields_tool_events(tmp_path):
    events = [
        FunctionToolCallEvent(
            part=ToolCallPart(tool_name="bash", tool_call_id="tc1", args='{"command": "ls"}')
        ),
        FunctionToolResultEvent(
            result=ToolReturnPart(tool_name="bash", tool_call_id="tc1", content="file1.txt")
        ),
        AgentRunResultEvent(result=_FakeResult(output="done")),
    ]
    agent = FakeAgent(events)
    svc = ChatService(agent=agent, deps=_make_deps(), store=_make_store(tmp_path))

    results = []
    async for ev in svc.send_message("run ls"):
        results.append(ev)

    assert len(results) == 4  # ToolStarted, ToolFinished, TextDelta fallback, Done
    assert isinstance(results[0], ToolStarted)
    assert results[0].tool_name == "bash"
    assert results[0].args == {"command": "ls"}
    assert isinstance(results[1], ToolFinished)
    assert results[1].ok is True
    assert isinstance(results[2], TextDelta)  # fallback when no text streamed
    assert results[2].content == "done"
    assert isinstance(results[3], Done)


@pytest.mark.asyncio
async def test_send_message_persists_on_done(tmp_path):
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(parts=[TextPart(content="result")]),
    ]
    events = [
        AgentRunResultEvent(result=_FakeResult(output="result", messages=messages)),
    ]
    agent = FakeAgent(events)
    store = _make_store(tmp_path)
    svc = ChatService(agent=agent, deps=_make_deps(), store=store)

    async for _ in svc.send_message("hi"):
        pass

    loaded = store.load()
    assert len(loaded) == 2


@pytest.mark.asyncio
async def test_send_message_does_not_persist_on_error(tmp_path):
    class TestError(Exception):
        pass

    async def broken_stream(*args, **kwargs):
        yield PartStartEvent(index=0, part=TextPart(content="partial"))
        raise TestError("boom")

    agent = FakeAgent([])
    agent.run_stream_events = broken_stream
    store = _make_store(tmp_path)
    svc = ChatService(agent=agent, deps=_make_deps(), store=store)

    results = []
    async for ev in svc.send_message("hi"):
        results.append(ev)

    assert isinstance(results[-1], Error)
    loaded = store.load()
    assert loaded == []


@pytest.mark.asyncio
async def test_send_message_handles_user_cancelled(tmp_path):
    async def cancelled_stream(*args, **kwargs):
        yield  # must yield to be an async generator
        raise UserCancelledError()

    agent = FakeAgent([])
    agent.run_stream_events = cancelled_stream
    store = _make_store(tmp_path)
    svc = ChatService(agent=agent, deps=_make_deps(), store=store)

    results = []
    async for ev in svc.send_message("hi"):
        results.append(ev)

    assert isinstance(results[0], Error)
    assert isinstance(results[0].exception, UserCancelledError)


@pytest.mark.asyncio
async def test_clear_empties_messages_and_persists(tmp_path):
    store = _make_store(tmp_path)
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(parts=[TextPart(content="result")]),
    ]
    store.save(messages)

    svc = ChatService(agent=FakeAgent([]), deps=_make_deps(), store=store)
    assert len(svc.messages) == 2

    svc.clear()
    assert svc.messages == []
    assert store.load() == []


@pytest.mark.asyncio
async def test_send_message_yields_text_fallback_when_no_stream(tmp_path):
    events = [
        AgentRunResultEvent(result=_FakeResult(output="direct output")),
    ]
    agent = FakeAgent(events)
    svc = ChatService(agent=agent, deps=_make_deps(), store=_make_store(tmp_path))

    results = []
    async for ev in svc.send_message("hi"):
        results.append(ev)

    assert isinstance(results[0], TextDelta)
    assert results[0].content == "direct output"
    assert isinstance(results[1], Done)


@pytest.mark.asyncio
async def test_messages_property_returns_copy(tmp_path):
    store = _make_store(tmp_path)
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(parts=[TextPart(content="result")]),
    ]
    store.save(messages)

    svc = ChatService(agent=FakeAgent([]), deps=_make_deps(), store=store)
    msgs = svc.messages
    assert len(msgs) == 2
    msgs.pop()
    assert len(svc.messages) == 2  # original unchanged


# ---- queue mode tests ----


@pytest.mark.asyncio
async def test_submit_returns_request_id_immediately(tmp_path):
    events = [
        AgentRunResultEvent(result=_FakeResult(output="done")),
    ]
    agent = FakeAgent(events)
    svc = ChatService(agent=agent, deps=_make_deps(), store=_make_store(tmp_path))

    rid = await svc.submit("hi")
    assert len(rid) == 8  # uuid4 hex[:8]
    assert svc._worker_task is not None


@pytest.mark.asyncio
async def test_submit_then_events_yields_chat_events(tmp_path):
    events = [
        PartStartEvent(index=0, part=TextPart(content="Hello")),
        AgentRunResultEvent(result=_FakeResult(output="Hello")),
    ]
    agent = FakeAgent(events)
    svc = ChatService(agent=agent, deps=_make_deps(), store=_make_store(tmp_path))

    rid = await svc.submit("hi")
    q = svc.events(rid)

    results = []
    while True:
        ev = await q.get()
        if ev is None:
            break
        results.append(ev)

    assert len(results) == 2  # TextDelta + Done
    assert isinstance(results[0], TextDelta)
    assert isinstance(results[1], Done)


@pytest.mark.asyncio
async def test_queue_serializes_requests(tmp_path):
    """两个请求排队，第二个等第一个跑完才开始。"""
    order: list[str] = []

    class OrderedAgent:
        def __init__(self, name, delay=0.01):
            self._name = name
            self._delay = delay

        async def run_stream_events(self, prompt, deps=None, message_history=None):
            await asyncio.sleep(self._delay)
            order.append(self._name)
            yield AgentRunResultEvent(result=_FakeResult(output=self._name))

    svc = ChatService(agent=OrderedAgent("tmp"), deps=_make_deps(), store=_make_store(tmp_path))
    # Replace agent to avoid using the temp one
    svc._agent = OrderedAgent("a", delay=0.05)

    rid1 = await svc.submit("first")
    q1 = svc.events(rid1)

    # Submit second BEFORE first finishes
    rid2 = await svc.submit("second")
    q2 = svc.events(rid2)

    # Wait for both
    async def drain(q):
        while True:
            ev = await q.get()
            if ev is None:
                return

    await drain(q1)
    await drain(q2)

    # Must be sequential: a then b
    assert order == ["a", "a"]
