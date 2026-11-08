"""A stand-in for the Anthropic client, so AI tests run offline, instantly and for free."""
import json
from types import SimpleNamespace

import anthropic
import httpx2

import ai_practice


def good_questions(n=5, topic_hint=""):
    """A valid model response: alternating multiple choice and short answer."""
    qs = []
    for i in range(n):
        if i % 2 == 0:
            qs.append({"question": f"{topic_hint}What is {i} + 2?", "answer_type": "multiple_choice",
                       "choices": [str(i + 2), str(i + 3), str(i + 4), str(i + 12)],
                       "correct_answer": str(i + 2), "explanation": f"{i} + 2 = {i + 2}."})
        else:
            qs.append({"question": f"{topic_hint}What is 3/4 of {4 * i}?", "answer_type": "short_answer",
                       "choices": [], "correct_answer": str(3 * i), "explanation": f"3/4 × {4 * i} = {3 * i}."})
    return {"questions": qs}


def api_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("error", response=httpx2.Response(status, request=request), body=None)


class FakeClient:
    """Replays queued responses. Each item is a dict (sent as JSON text), a raw string,
    an exception to raise, or a ("refusal", None) tuple."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(request)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple) and item[0] == "refusal":
            return SimpleNamespace(stop_reason="refusal", content=[])
        text = item if isinstance(item, str) else json.dumps(item)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


def install(test_case, *responses):
    """Use a FakeClient for the rest of this test; returns it so tests can inspect requests."""
    fake = FakeClient(*responses)
    original = ai_practice.get_client
    ai_practice.get_client = lambda: fake
    test_case.addCleanup(setattr, ai_practice, "get_client", original)
    return fake


__all__ = ["FakeClient", "install", "good_questions", "api_error", "anthropic"]
