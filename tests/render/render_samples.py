"""Paths and loaders for the render tests (a uniquely named module: `conftest` is ambiguous across test folders)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "tests/fixtures/contract_sample"
GOLDEN = Path(__file__).resolve().parent / "golden"


def load(name: str):
    return json.loads((SAMPLE / name).read_text(encoding="utf-8"))


def text(name: str) -> str:
    return (SAMPLE / name).read_text(encoding="utf-8")
