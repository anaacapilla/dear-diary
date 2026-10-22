"""Offline metrics for JSONL evaluation predictions."""
import argparse
import json
import re
import statistics
import unicodedata
from pathlib import Path


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def words(text):
    return re.findall(r"\w+", normalize(text), flags=re.UNICODE)


def edit_distance(reference, prediction):
    previous = list(range(len(prediction) + 1))
    for i, ref in enumerate(reference, 1):
        current = [i]
        for j, pred in enumerate(prediction, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (ref != pred)))
        previous = current
    return previous[-1]


def set_counts(reference, prediction):
    ref = {normalize(x) for x in reference}
    pred = {normalize(x) for x in prediction}
    return len(ref & pred), len(pred - ref), len(ref - pred)


def prf(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    return {"precision": precision, "recall": recall, "f1": f1,
            "true_positive": tp, "false_positive": fp, "false_negative": fn}


def score(rows):
    errors = sum(bool(r.get("error")) for r in rows)
    successful = [r for r in rows if not r.get("error")]
    sentiment_rows = [r for r in rows if "sentiment" in r.get("expected", {})]
    result = {
        "samples": len(rows), "failures": errors,
        "failure_rate": errors / len(rows) if rows else None,
        # Failed annotated examples count as incorrect, never disappear from accuracy.
        "sentiment_accuracy": (
            sum(r.get("prediction", {}).get("sentiment") == r["expected"]["sentiment"]
                for r in sentiment_rows) / len(sentiment_rows)
            if sentiment_rows else None
        ),
        "sentiment_samples": len(sentiment_rows),
    }
    for field in ("people", "places", "emotions"):
        tp = fp = fn = annotated = 0
        for row in rows:
            if field not in row.get("expected", {}):
                continue
            annotated += 1
            counts = set_counts(row["expected"][field], row.get("prediction", {}).get(field, []))
            tp, fp, fn = [a + b for a, b in zip((tp, fp, fn), counts)]
        result[field] = {**prf(tp, fp, fn), "annotated_samples": annotated}
    distance = reference_words = audio_samples = hallucinated_silences = 0
    for row in rows:
        if not row.get("audio") or row.get("transcription") is None:
            continue
        ref = words(row.get("reference_transcription", ""))
        hyp = words(row["transcription"])
        audio_samples += 1
        distance += edit_distance(ref, hyp)
        reference_words += len(ref)
        if not ref and hyp:
            hallucinated_silences += 1
    result["transcription"] = {
        "wer": distance / reference_words if reference_words else None,
        "word_errors": distance, "reference_words": reference_words,
        "completed_audio_samples": audio_samples,
        "total_audio_samples": sum(bool(r.get("audio")) for r in rows),
        "silence_hallucinations": hallucinated_silences,
    }
    for key in ("transcription_seconds", "analysis_seconds", "total_seconds"):
        values = sorted(r[key] for r in successful if r.get(key) is not None)
        result[key] = {
            "mean": statistics.mean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "samples": len(values),
        }
    result["summary_faithfulness"] = "Requiere revisión humana; no se infiere del formato JSON."
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.predictions.read_text(encoding="utf-8").splitlines() if line.strip()]
    text = json.dumps(score(rows), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
