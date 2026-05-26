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

from cairn.core.chat_events import Done, Error, TextDelta, ToolFinished, ToolStarted
from cairn.core.chat_service import ChatService
from cairn.core.config import load_config
from cairn.core.models import Config, cairnDeps, Provider, UserCancelledError
from cairn.core.persistence import MessageStore


def _make_deps() -> cairnDeps:
    return cairnDeps(config=Config(provider=Provider.ANTHROPIC, model="test-model", api_key="test-key"), cwd="/tmp/test", fs=None)


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

    await svc.clear()
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
async def test_submit_then_broadcast_yields_chat_events(tmp_path):
    events = [
        PartStartEvent(index=0, part=TextPart(content="Hello")),
        AgentRunResultEvent(result=_FakeResult(output="Hello")),
    ]
    agent = FakeAgent(events)
    svc = ChatService(agent=agent, deps=_make_deps(), store=_make_store(tmp_path))

    rid = await svc.submit("hi")

    # Read from broadcast queue
    chat_events = []
    while True:
        wrapped = await svc._broadcast.get()
        if wrapped["kind"] == "chat_event" and wrapped["request_id"] == rid:
            chat_events.append(wrapped["event"])
        if wrapped["kind"] == "request_finished" and wrapped["request_id"] == rid:
            break

    assert len(chat_events) == 2  # TextDelta + Done
    assert isinstance(chat_events[0], TextDelta)
    assert isinstance(chat_events[1], Done)


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
    svc._agent = OrderedAgent("a", delay=0.05)

    rid1 = await svc.submit("first")
    rid2 = await svc.submit("second")

    # Drain broadcast: expect 2 request_started + 2 request_finished + events
    finished = 0
    while finished < 2:
        wrapped = await svc._broadcast.get()
        if wrapped["kind"] == "request_finished":
            finished += 1

    # Must be sequential: a then a
    assert order == ["a", "a"]


# ---- rollback tests ----


@pytest.mark.asyncio
async def test_rollback_truncates_history(tmp_path):
    store = _make_store(tmp_path)
    messages = [
        ModelRequest(parts=[UserPromptPart(content="msg1")]),
        ModelResponse(parts=[TextPart(content="r1")]),
        ModelRequest(parts=[UserPromptPart(content="msg2")]),
        ModelResponse(parts=[TextPart(content="r2")]),
        ModelRequest(parts=[UserPromptPart(content="msg3")]),
        ModelResponse(parts=[TextPart(content="r3")]),
    ]
    store.save(messages)

    svc = ChatService(agent=FakeAgent([]), deps=_make_deps(), store=store)
    assert len(svc.messages) == 6

    ok = await svc.rollback_to(2)
    assert ok is True
    assert len(svc.messages) == 2
    assert store.load()[0].parts[0].content == "msg1"


@pytest.mark.asyncio
async def test_rollback_snaps_to_turn_boundary(tmp_path):
    """点击 AI 消息 (index=3) 应自动捕捉到前一个用户消息 (index=2)。"""
    store = _make_store(tmp_path)
    messages = [
        ModelRequest(parts=[UserPromptPart(content="turn1")]),
        ModelResponse(parts=[TextPart(content="r1")]),
        ModelRequest(parts=[UserPromptPart(content="turn2")]),
        ModelResponse(parts=[TextPart(content="r2")]),
        ModelRequest(parts=[UserPromptPart(content="turn3")]),
        ModelResponse(parts=[TextPart(content="r3")]),
    ]
    store.save(messages)

    svc = ChatService(agent=FakeAgent([]), deps=_make_deps(), store=store)

    # Click AI message at index 3 → should snap to user message at index 2
    ok = await svc.rollback_to(3)
    assert ok is True
    assert len(svc.messages) == 2  # kept turn1 user+AI only


@pytest.mark.asyncio
async def test_rollback_rejected_when_busy(tmp_path):
    svc = ChatService(agent=FakeAgent([AgentRunResultEvent(result=_FakeResult(output="ok"))]), deps=_make_deps(), store=_make_store(tmp_path))
    svc._busy = True

    ok = await svc.rollback_to(0)
    assert ok is False
    assert len(svc.messages) == 0  # unchanged


@pytest.mark.asyncio
async def test_rollback_publishes_snapshot(tmp_path):
    store = _make_store(tmp_path)
    messages = [
        ModelRequest(parts=[UserPromptPart(content="a")]),
        ModelResponse(parts=[TextPart(content="b")]),
        ModelRequest(parts=[UserPromptPart(content="c")]),
        ModelResponse(parts=[TextPart(content="d")]),
    ]
    store.save(messages)

    svc = ChatService(agent=FakeAgent([]), deps=_make_deps(), store=store)

    ok = await svc.rollback_to(2)
    assert ok is True

    # Should have history_snapshot in broadcast
    wrapped = await svc._broadcast.get()
    assert wrapped["kind"] == "history_snapshot"
    assert wrapped["message_count"] == 2
