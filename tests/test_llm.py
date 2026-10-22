import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from src.llm.extractor import DiaryExtractor, dividir_texto
from src.llm.models import DiaryAnalysis


def response(data):
    return NS(message=NS(content=json.dumps(data, ensure_ascii=False)))


def test_invalid_json_retried_without_logging_content(analysis, caplog):
    client = Mock()
    client.chat.side_effect = [response({"private": "SECRETO"}), response(analysis.model_dump())]
    result = DiaryExtractor(client=client).extract("SECRETO Ana Madrid")
    assert result.people == ["Ana"]
    assert client.chat.call_count == 2
    assert "SECRETO" not in caplog.text


def test_two_invalid_responses_fail_cleanly():
    client = Mock()
    client.chat.return_value = response({})
    with pytest.raises(RuntimeError):
        DiaryExtractor(client=client).extract("texto")


def test_chunking_preserves_all_characters():
    text = "una palabra " * 3000
    parts = list(dividir_texto(text, 500))
    assert "".join(parts) == text
    assert max(map(len, parts)) <= 500


def test_empty_input_never_calls_llm():
    client = Mock()
    with pytest.raises(ValueError):
        DiaryExtractor(client=client).extract("  ")
    client.chat.assert_not_called()


def test_long_entry_is_analyzed_in_chunks_and_synthesized(analysis):
    client = Mock()
    client.chat.side_effect = lambda **kw: response(
        {"summary": "Síntesis"} if "DailySummary" == kw["format"]["title"]
        else analysis.model_dump()
    )
    result = DiaryExtractor(client=client).extract("Ana Madrid paseo " * 2000)
    assert result.summary == "Síntesis"
    assert result.people == ["Ana"]
    assert client.chat.call_count >= 3


def test_accented_joy_is_accepted(analysis):
    data = analysis.model_dump()
    data["emotions"] = ["alegría"]
    assert DiaryAnalysis.model_validate(data).emotions == ["alegria"]


def test_entity_grounding_rejects_invented_names_and_author():
    from src.llm.extractor import menciones_literales
    assert menciones_literales(["Ana", "Pedro", "yo", "narrador"],
                              "Yo he visto a Ana.", people=True) == ["Ana"]
    assert menciones_literales(["Ana"], "Lo haré mañana", people=True) == []
