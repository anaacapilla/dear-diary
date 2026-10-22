from unittest.mock import Mock

from evaluation.score import edit_distance, score
from evaluation.run import run_sample


def test_wer_and_extraction_metrics_are_computed():
    rows = [{
        "audio": "test.wav", "reference_transcription": "hoy fui a casa",
        "transcription": "hoy voy a casa",
        "expected": {"people": ["Ana"], "places": ["casa"], "sentiment": "positivo"},
        "prediction": {"people": ["Ana", "Luis"], "places": [], "sentiment": "positivo"},
    }]
    result = score(rows)
    assert result["transcription"]["wer"] == 0.25
    assert result["people"]["precision"] == 0.5
    assert result["people"]["recall"] == 1
    assert result["places"]["f1"] == 0
    assert result["sentiment_accuracy"] == 1


def test_failed_samples_are_not_excluded_from_accuracy():
    result = score([{"error": {"type": "RuntimeError"},
                     "expected": {"sentiment": "positivo", "people": ["Ana"]}}])
    assert result["sentiment_accuracy"] == 0
    assert result["people"]["false_negative"] == 1
    assert result["failure_rate"] == 1


def test_no_reference_is_not_reported_as_perfect():
    assert score([])["sentiment_accuracy"] is None
    assert score([])["transcription"]["wer"] is None
    assert score([])["people"]["f1"] is None


def test_silence_is_measured_without_division_by_zero():
    result = score([{"audio": "silence.wav", "reference_transcription": "",
                     "transcription": "gracias por ver"}])
    assert result["transcription"]["wer"] is None
    assert result["transcription"]["silence_hallucinations"] == 1


def test_silent_sample_skips_llm(tmp_path):
    extractor = Mock()
    row = run_sample({"id": "silence", "text": ""}, tmp_path, extractor)
    assert row["error"] is None
    extractor.extract.assert_not_called()
