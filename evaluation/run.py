"""Run an explicitly selected local dataset. Never opens diary SQLite."""
import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from src.config import DIARY_TIMEZONE, OLLAMA_MODEL, PROJECT_ROOT, WHISPER_MODEL
from src.llm.extractor import DiaryExtractor, INFERENCE_OPTIONS
from src.llm.prompts import DAILY_SYSTEM_PROMPT, PROMPT_VERSION, SYSTEM_PROMPT
from src.transcriber import transcribir


def run_sample(sample, base_dir, extractor):
    result = {
        "id": sample["id"], "expected": sample.get("expected", {}),
        "audio": sample.get("audio"),
        "reference_transcription": sample.get("reference_transcription", sample.get("text", "")),
        "prediction": {}, "transcription": None, "error": None,
        "transcription_seconds": None, "analysis_seconds": None,
    }
    started = time.perf_counter()
    stage = "transcription"
    try:
        if sample.get("audio"):
            if "reference_transcription" not in sample:
                raise ValueError("El audio necesita una transcripción de referencia")
            audio_path = (base_dir / sample["audio"]).resolve()
            tick = time.perf_counter()
            text = transcribir(audio_path)
            result["transcription_seconds"] = time.perf_counter() - tick
        else:
            text = sample["text"]
        result["transcription"] = text
        if text.strip():
            stage = "analysis"
            tick = time.perf_counter()
            result["prediction"] = extractor.extract(text).model_dump()
            result["analysis_seconds"] = time.perf_counter() - tick
    except Exception as error:
        result["error"] = {"stage": stage, "type": type(error).__name__}
    result["total_seconds"] = time.perf_counter() - started
    return result


def experiment_metadata(dataset, extractor):
    digest = None
    try:
        digest = next((m.digest for m in extractor.client.list().models
                       if m.model == OLLAMA_MODEL), None)
    except Exception:
        pass
    git = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
        capture_output=True, text=True, check=False,
    )
    sources = [
        "src/llm/extractor.py", "src/llm/prompts.py", "src/llm/models.py",
        "src/transcriber.py", "evaluation/run.py", "evaluation/score.py",
    ]
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset.resolve()),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "python": platform.python_version(), "platform": platform.platform(),
        "whisper_model": WHISPER_MODEL, "ollama_model": OLLAMA_MODEL,
        "ollama_digest": digest, "prompt_version": PROMPT_VERSION,
        "system_prompt": SYSTEM_PROMPT, "daily_system_prompt": DAILY_SYSTEM_PROMPT,
        "inference_options": INFERENCE_OPTIONS, "think": False,
        "diary_timezone": DIARY_TIMEZONE,
        "git_revision": git.stdout.strip() if git.returncode == 0 else None,
        # Source snapshots exclude .env and all user content, and preserve uncommitted experiments.
        "source_snapshot": {name: (PROJECT_ROOT / name).read_text(encoding="utf-8") for name in sources},
        "packages": {p: importlib.metadata.version(p)
                     for p in ("faster-whisper", "ctranslate2", "ollama", "pydantic")},
        "note": "Registra también el hardware y las condiciones acústicas del experimento.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/synthetic.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    samples = [json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len({s["id"] for s in samples}) != len(samples):
        raise ValueError("Los identificadores del corpus deben ser únicos")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    metadata_path = args.output.with_suffix(args.output.suffix + ".metadata.json")
    if args.output.exists() or metadata_path.exists():
        raise FileExistsError("El experimento ya existe: elige otro nombre de salida")
    extractor = DiaryExtractor()
    metadata = experiment_metadata(args.dataset, extractor)
    with metadata_path.open("x", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with args.output.open("x", encoding="utf-8") as handle:
        for i, sample in enumerate(samples, 1):
            result = run_sample(sample, args.dataset.resolve().parent, extractor)
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            handle.flush()
            print(f"Muestra {i}/{len(samples)}: {'fallo' if result['error'] else 'completada'}", flush=True)


if __name__ == "__main__":
    main()
