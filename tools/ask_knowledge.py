import argparse
import json
from pathlib import Path
from backend.services.rag.answers import RAGAnswerService


def main():
    parser = argparse.ArgumentParser(description="Retrieve evidence and generate a cited hospital service answer")
    parser.add_argument("question")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = RAGAnswerService.from_env().answer(args.question, args.top_k)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
