import copy
import json
import uuid

import pytest

from cricket.context import ContextBudgetExceeded, build_context, estimate_tokens
from cricket.conversations import ConversationRepository
from cricket.harness import SYSTEM_PROMPT, AgentHarness
from cricket.settings import HarnessSettings
from tools import TOOLS


class FakeModel:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply(kwargs) if callable(reply) else reply


def tool_reply(arguments='{"query":"Delhi"}', name="find_espn_player", call_id="call1"):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}},
        ],
    }


def repository(session):
    repo = ConversationRepository(session, uuid.uuid4())
    repo.create(SYSTEM_PROMPT)
    return repo


def assert_paired(messages):
    requested, results = [], []
    for payload in messages:
        requested.extend(call["id"] for call in payload.get("tool_calls", []))
        if payload["role"] == "tool":
            results.append(payload["tool_call_id"])
    assert requested == results


def test_successful_exchange_and_final_answer_are_persisted(session):
    repo = repository(session)
    model = FakeModel(tool_reply(), {"content": "Delhi is warm."})
    harness = AgentHarness(completion=model, runner=lambda name, args: '{"temp_f":80}')
    response, traces = harness.run(repo, "Weather in Delhi?")
    assert response == "Delhi is warm."
    assert set(traces[0]) == {"name", "args", "result"}
    assert json.loads(traces[0]["result"])["data"] == {"temp_f": 80}
    assert [m.role for m in repo.messages()] == ["system", "user", "assistant", "tool", "assistant"]
    assert_paired([m.payload for m in repo.messages()])
    assert_paired(model.requests[1]["messages"])
    call = repo.tool_calls()[0][0]
    assert call.status == "complete" and call.request_message_id and call.result_message_id


@pytest.mark.parametrize(
    "args",
    [
        "{broken",
        "[]",
        "null",
        '{"query":42}',
        '{"query":"Delhi","extra":true}',
        "{}",
        '{"query":NaN}',
        '{"query":1e309}',
        '{"query":"\\ud800"}',
        '{"query":"\\u0000"}',
    ],
)
def test_invalid_arguments_are_traced_without_executing_tools(session, args):
    repo = repository(session)
    model = FakeModel(tool_reply(args), {"content": "Please supply a city."})
    executions = []
    harness = AgentHarness(completion=model, runner=lambda *args: executions.append(args))
    _, traces = harness.run(repo, "Weather?")
    assert not executions
    assert json.loads(traces[0]["result"])["error"]["code"] == "invalid_arguments"
    assert repo.tool_calls()[0][0].status == "failed"
    assert_paired([m.payload for m in repo.messages()])


def test_unknown_tool_is_not_executed(session):
    repo = repository(session)
    model = FakeModel(tool_reply(name="delete_everything"), {"content": "Use a known tool."})
    executions = []
    _, traces = AgentHarness(completion=model, runner=lambda *a: executions.append(a)).run(
        repo, "Help"
    )
    assert not executions
    assert json.loads(traces[0]["result"])["error"]["code"] == "unknown_tool"


def test_completed_tool_trace_survives_later_model_failure_without_secret_leak(session):
    repo = repository(session)
    model = FakeModel(tool_reply(), RuntimeError("credential=do-not-expose"))
    response, traces = AgentHarness(completion=model, runner=lambda *a: '{"temp_f":80}').run(
        repo, "Hi"
    )
    assert "unavailable" in response and "do-not-expose" not in response
    assert len(traces) == 1 and json.loads(traces[0]["result"])["ok"]
    assert repo.messages()[-1].payload["content"] == response
    assert "do-not-expose" not in json.dumps(repo.transcript(), default=str)


def test_provider_failure_is_traced_and_model_can_recover(session):
    repo = repository(session)

    def failed_provider(*args):
        raise RuntimeError("password=do-not-expose")

    model = FakeModel(tool_reply(), {"content": "The weather provider is unavailable."})
    response, traces = AgentHarness(completion=model, runner=failed_provider).run(repo, "Hi")
    assert "unavailable" in response
    assert json.loads(traces[0]["result"])["error"]["code"] == "tool_failed"
    assert "do-not-expose" not in json.dumps(repo.transcript(), default=str)
    assert repo.tool_calls()[0][0].status == "failed"


def test_safe_domain_failure_is_preserved_in_model_context_and_saved_trace(session):
    repo = repository(session)
    expected = {
        "ok": False,
        "data": {"candidates": [{"id": "a", "name": "Same Name"}]},
        "error": {"code": "ambiguous_player", "message": "Choose a player ID."},
        "provenance": [{"provider": "fixture"}],
        "coverage": {"matches": 0},
    }
    model = FakeModel(tool_reply(), {"content": "Please choose a candidate."})
    _, traces = AgentHarness(completion=model, runner=lambda *args: expected).run(repo, "Help")
    assert json.loads(traces[0]["result"]) == expected
    assert json.loads(model.requests[1]["messages"][-1]["content"]) == expected
    assert repo.tool_calls()[0][0].result == expected
    assert repo.tool_calls()[0][0].status == "failed"


@pytest.mark.parametrize(
    "invalid",
    [
        {"ok": False, "error": "password=do-not-expose"},
        {"ok": False, "error": {"code": [], "message": "password=do-not-expose"}},
        {"ok": False, "error": {"code": "invalid code", "message": "password=do-not-expose"}},
        {"ok": False, "error": {"code": "failed", "message": ""}},
        {"ok": False, "error": {"code": "failed", "message": "x" * 2001}},
        {"ok": False, "error": {"code": "failed", "message": "Safe"}, "coverage": "secret"},
        {"ok": True, "error": {"code": "failed", "message": "password=do-not-expose"}},
    ],
)
def test_malformed_result_envelopes_are_sanitized(session, invalid):
    repo = repository(session)
    model = FakeModel(tool_reply(), {"content": "Retry"})
    _, traces = AgentHarness(completion=model, runner=lambda *args: invalid).run(repo, "Help")
    actual = json.loads(traces[0]["result"])
    assert not actual["ok"] and actual["error"]["code"] == "tool_failed"
    assert "do-not-expose" not in json.dumps(repo.transcript(), default=str)


def test_five_round_limit_persists_all_results_and_final_limit_message(session):
    repo = repository(session)
    model = FakeModel(*(tool_reply() for _ in range(5)))
    response, traces = AgentHarness(completion=model, runner=lambda *a: "{}").run(repo, "Hi")
    assert len(model.requests) == len(traces) == 5
    assert "tool-call limit" in response
    assert len({call.model_call_id for call, _ in repo.tool_calls()}) == 5
    assert_paired([m.payload for m in repo.messages()])
    assert repo.messages()[-1].role == "assistant"


def test_multiple_calls_preserve_order_and_unique_ids(session):
    repo = repository(session)
    reply = tool_reply(call_id="same")
    reply["tool_calls"].append(tool_reply('{"query":"Mumbai"}', call_id="same")["tool_calls"][0])
    model = FakeModel(reply, {"content": "Done."})
    _, traces = AgentHarness(completion=model, runner=lambda name, args: json.dumps(args)).run(
        repo, "Hi"
    )
    assert [call["args"]["query"] for call in traces] == ["Delhi", "Mumbai"]
    assert len({call.model_call_id for call, _ in repo.tool_calls()}) == 2
    assert_paired([m.payload for m in repo.messages()])


def test_oversized_tool_result_preserves_trace_and_rejects_next_model_call(session):
    repo = repository(session)
    model = FakeModel(tool_reply())
    initial = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Hi"},
    ]
    harness = AgentHarness(
        settings=HarnessSettings(context_token_budget=estimate_tokens(initial, TOOLS) + 200),
        completion=model,
        runner=lambda *a: json.dumps({"data": "x" * 4000}),
    )
    response, traces = harness.run(repo, "Hi")
    assert "context budget" in response and len(model.requests) == 1
    assert "x" * 4000 in traces[0]["result"]
    assert_paired([m.payload for m in repo.messages()])


def seed_complete_turn(repo, number):
    turn = repo.start_turn(f"Question {number}: Virat Kohli, ODI, ba607b88")
    payload = tool_reply(call_id=f"seed{number}")
    args = {"query": "Delhi"}
    calls = repo.begin_exchange(turn, payload, [args])
    repo.finish_tool(
        calls[0],
        {"ok": True, "data": {"note": "x" * 150}, "error": None, "provenance": [], "coverage": {}},
    )
    repo.finish_turn(turn, f"Answer {number}")


def summarization_budget(repo, message):
    """Fit the last ten seeded turns and a summary, but not all eleven original turns."""
    recent = [m.payload for m in repo.messages() if m.turn_number >= 2]
    required = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *recent,
        {"role": "user", "content": message},
    ]
    full = [required[0], *[m.payload for m in repo.messages() if m.turn_number], required[-1]]
    required_size = estimate_tokens(required, TOOLS)
    full_size = estimate_tokens(full, TOOLS)
    assert full_size > required_size
    budget = required_size + min(200, (full_size - required_size) // 2)
    return budget


@pytest.mark.parametrize("invalid", ["bad\x00summary", "bad\ud800summary"])
def test_invalid_callback_summary_never_reaches_counting_or_storage(session, invalid):
    repo = repository(session)
    for number in range(1, 12):
        seed_complete_turn(repo, number)
    budget = summarization_budget(repo, "Continue")
    turn = repo.start_turn("Continue")
    with pytest.raises(ContextBudgetExceeded, match="summarization failed"):
        build_context(repo, turn, SYSTEM_PROMPT, TOOLS, budget, lambda *args: invalid)
    assert repo.conversation.summary is None and repo.conversation.summary_through_turn == 0


def test_summary_preserves_ten_complete_turns_and_full_transcript(session):
    repo = repository(session)
    for number in range(1, 13):
        seed_complete_turn(repo, number)
    turn = repo.start_turn("Only T20Is now")
    original_count = len(repo.messages())
    summaries = []

    def summarize(previous, payloads, available):
        assert_paired(payloads)
        summaries.append(payloads)
        return "Player Virat Kohli (ba607b88); previous format ODI."

    recent = [m.payload for m in repo.messages() if 3 <= m.turn_number <= 13]
    required = [{"role": "system", "content": SYSTEM_PROMPT}, *recent]
    expected = [
        required[0],
        {
            "role": "system",
            "content": "Earlier conversation summary:\n"
            "Player Virat Kohli (ba607b88); previous format ODI.",
        },
        *recent,
    ]
    budget = estimate_tokens(expected, TOOLS)
    assert budget >= estimate_tokens(required, TOOLS)
    assert estimate_tokens([m.payload for m in repo.messages()], TOOLS) > budget
    context = build_context(repo, turn, SYSTEM_PROMPT, TOOLS, budget, summarize)
    assert len(summaries) == 2
    assert repo.conversation.summary_through_turn == 2
    assert len(repo.messages()) == original_count
    assert estimate_tokens(context, TOOLS) <= budget
    assert context[1]["content"].startswith("Earlier conversation summary:")
    assert [m["content"] for m in context if m["role"] == "user"] == [
        *(f"Question {number}: Virat Kohli, ODI, ba607b88" for number in range(3, 13)),
        "Only T20Is now",
    ]
    assert_paired(context)
    build_context(repo, turn, SYSTEM_PROMPT, TOOLS, budget, summarize)
    assert len(summaries) == 2


def test_summarization_failure_leaves_complete_history_and_checkpoints_unchanged(session):
    repo = repository(session)
    for number in range(1, 12):
        seed_complete_turn(repo, number)
    model = FakeModel(RuntimeError("secret"))
    response, _ = AgentHarness(
        settings=HarnessSettings(context_token_budget=summarization_budget(repo, "Continue")),
        completion=model,
    ).run(repo, "Continue")
    assert "unavailable" in response and "secret" not in response
    assert repo.conversation.summary_through_turn == 0 and repo.conversation.summary is None
    assert len([m for m in repo.messages() if m.role == "user"]) == 12
    assert_paired([m.payload for m in repo.messages()])
    assert "tools" not in model.requests[0]


def test_model_summary_is_checkpointed_and_used_for_followup(session):
    repo = repository(session)
    for number in range(1, 12):
        seed_complete_turn(repo, number)
    model = FakeModel(
        {"content": "Virat Kohli ba607b88, ODI, available matches only."},
        {"content": "Kohli's T20Is next."},
    )
    harness = AgentHarness(
        settings=HarnessSettings(context_token_budget=summarization_budget(repo, "Only T20Is")),
        completion=model,
    )
    response, _ = harness.run(repo, "Only T20Is")
    assert response == "Kohli's T20Is next."
    assert "tools" not in model.requests[0]  # Summarization never executes tools.
    original = json.loads(model.requests[0]["messages"][1]["content"])["completed_turn"]
    assert_paired(original)
    assert "ba607b88" in model.requests[1]["messages"][1]["content"]
    assert repo.conversation.summary_through_turn == 1
    assert len(repo.messages()) == 47
    assert_paired(model.requests[1]["messages"])


def test_under_budget_history_is_kept_exact_without_summary_provider_work(session):
    repo = repository(session)
    for number in range(1, 12):
        turn = repo.start_turn(f"Question {number}")
        repo.finish_turn(turn, f"Answer {number}")
    model = FakeModel({"content": "Continued"})
    response, _ = AgentHarness(completion=model).run(repo, "Continue")
    assert response == "Continued" and len(model.requests) == 1
    assert model.requests[0]["tools"] == TOOLS
    assert len([m for m in model.requests[0]["messages"] if m["role"] == "user"]) == 12
    assert repo.conversation.summary is None and repo.conversation.summary_through_turn == 0


def test_short_tool_heavy_conversation_does_not_exhaust_twelve_thousand_tokens(session):
    repo = repository(session)
    for number in range(4):
        turn = repo.start_turn(f"Question {number}: chart Kohli's ODI runs")
        calls = repo.begin_exchange(
            turn, tool_reply(call_id=f"chart{number}"), [{"query": "Delhi"}]
        )
        repo.finish_tool(
            calls[0],
            {
                "ok": True,
                "data": {"note": "sample data " * 140},
                "error": None,
                "provenance": [],
                "coverage": {},
            },
        )
        repo.finish_turn(turn, "Available-match results. " * 20)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *[row.payload for row in repo.messages() if row.turn_number],
        {"role": "user", "content": "okay show"},
    ]
    old_byte_count = len(json.dumps({"messages": messages, "tools": TOOLS}).encode()) + 32 * len(
        messages
    )
    assert old_byte_count > 12000
    assert estimate_tokens(messages, TOOLS) < 12000
    model = FakeModel({"content": "Here is the supported chart."})
    response, _ = AgentHarness(completion=model).run(repo, "okay show")
    assert response == "Here is the supported chart."
    assert len(model.requests) == 1
    assert repo.conversation.summary is None


def test_token_count_failure_uses_safe_byte_fallback(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("local tokenizer unavailable")

    monkeypatch.setattr("litellm.token_counter", fail)
    messages = [{"role": "user", "content": "Some Unicode 🏏"}]
    byte_count = len(
        json.dumps({"messages": messages, "tools": TOOLS}, ensure_ascii=False).encode()
    )
    assert estimate_tokens(messages, TOOLS) >= byte_count


def test_under_budget_checkpoint_keeps_new_older_turns_exact(session):
    repo = repository(session)
    for number in range(1, 13):
        seed_complete_turn(repo, number)
    repo.save_summary("First turn: Kohli ba607b88 ODI", 1)
    turn = repo.start_turn("Continue")
    context = build_context(
        repo,
        turn,
        SYSTEM_PROMPT,
        TOOLS,
        20000,
        lambda *args: pytest.fail("Under-budget context must not invoke summarization"),
    )
    assert repo.conversation.summary_through_turn == 1
    assert "Question 2" in json.dumps(context)
    assert not any((m.get("content") or "").startswith("Question 1:") for m in context)
    assert "ba607b88" in context[1]["content"]
    assert_paired(context)


def test_context_compacts_only_as_many_older_turns_as_needed(session):
    repo = repository(session)
    for number in range(1, 13):
        turn = repo.start_turn("x" * 1000 if number == 1 else f"Question {number}")
        repo.finish_turn(turn, f"Answer {number}")
    turn = repo.start_turn("Continue")
    summary = "First turn summarized"
    expected = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": "Earlier conversation summary:\n" + summary},
        *[m.payload for m in repo.messages() if m.turn_number >= 2],
    ]
    budget = estimate_tokens(expected, TOOLS)
    summarized = []

    def summarize(previous, payloads, reserve):
        summarized.append(payloads)
        return summary

    context = build_context(repo, turn, SYSTEM_PROMPT, TOOLS, budget, summarize)
    assert len(summarized) == 1 and repo.conversation.summary_through_turn == 1
    assert "Question 2" in json.dumps(context)
    assert estimate_tokens(context, TOOLS) == budget
    assert build_context(repo, turn, SYSTEM_PROMPT, TOOLS, budget, summarize) == context
    assert len(summarized) == 1


def test_context_that_fits_exact_budget_needs_no_summary_reserve(session):
    repo = repository(session)
    budget = estimate_tokens(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "Hi"}], TOOLS
    )
    model = FakeModel({"content": "Hello"})
    response, _ = AgentHarness(
        settings=HarnessSettings(context_token_budget=budget), completion=model
    ).run(repo, "Hi")
    assert response == "Hello" and len(model.requests) == 1


def test_recent_turns_over_budget_are_never_partially_trimmed(session):
    repo = repository(session)
    long_question = ",".join(str(number) for number in range(3000))
    number = repo.start_turn(long_question)
    repo.finish_turn(number, "Saved answer")
    required = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *[row.payload for row in repo.messages() if row.turn_number],
        {"role": "user", "content": "Continue"},
    ]
    model = FakeModel()
    response, _ = AgentHarness(
        settings=HarnessSettings(context_token_budget=estimate_tokens(required, TOOLS) - 1),
        completion=model,
    ).run(repo, "Continue")
    assert "context budget" in response and not model.requests
    assert repo.messages()[1].payload["content"] == long_question


def test_invalid_harness_configuration(monkeypatch):
    monkeypatch.setenv("MAX_TOOL_ROUNDS", "6")
    with pytest.raises(ValueError, match="between 1 and 5"):
        HarnessSettings.from_env()
    monkeypatch.setenv("MAX_TOOL_ROUNDS", "invalid")
    with pytest.raises(ValueError, match="integers"):
        HarnessSettings.from_env()
