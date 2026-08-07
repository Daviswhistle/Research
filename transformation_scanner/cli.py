from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .dart_client import DartClient, DartClientError
from .models import AlertBand
from .pipeline import TransformationPipeline
from .report import write_outputs
from .store import ScannerStore


_BAND_RANK = {
    AlertBand.LOW: 0,
    AlertBand.WATCH: 1,
    AlertBand.REVIEW: 2,
    AlertBand.URGENT: 3,
}


def _today_kst() -> datetime:
    return datetime.now(ZoneInfo("Asia/Seoul"))


def _default_start_date() -> str:
    return (_today_kst() - timedelta(days=1)).strftime("%Y%m%d")


def _default_end_date() -> str:
    return _today_kst().strftime("%Y%m%d")


def _csv_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value).split(",") if item.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OpenDART 공시에서 기업 성격이 바뀔 가능성이 큰 사건을 탐색합니다.",
    )
    parser.add_argument("--start", default=_default_start_date(), help="검색 시작일 YYYYMMDD")
    parser.add_argument("--end", default=_default_end_date(), help="검색 종료일 YYYYMMDD")
    parser.add_argument("--api-key", default="", help="미지정 시 DART_API_KEY 환경변수 사용")
    parser.add_argument("--corp-classes", default="Y,K", help="Y=유가, K=코스닥")
    parser.add_argument("--public-types", default="B,C,E,I", help="OpenDART 공시유형")
    parser.add_argument(
        "--state-db",
        default="output/transformation_scanner/state.sqlite3",
        help="중복 방지 SQLite 경로",
    )
    parser.add_argument(
        "--output-dir",
        default="output/transformation_scanner",
        help="JSONL·Markdown 산출물 경로",
    )
    parser.add_argument(
        "--min-band",
        choices=[band.value for band in AlertBand],
        default=AlertBand.WATCH.value,
        help="리포트에 포함할 최소 등급",
    )
    parser.add_argument("--include-documents", action="store_true", help="DART 원문 ZIP도 파싱")
    parser.add_argument("--include-seen", action="store_true", help="이미 처리한 접수번호도 재평가")
    parser.add_argument("--no-persist", action="store_true", help="신규 결과를 SQLite에 저장하지 않음")
    parser.add_argument("--no-state", action="store_true", help="SQLite를 읽거나 쓰지 않음")
    parser.add_argument("--cluster-lookback-days", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    api_key = str(args.api_key or os.environ.get("DART_API_KEY") or os.environ.get("OPENDART_API_KEY") or "").strip()
    if not api_key:
        print("DART_API_KEY 또는 OPENDART_API_KEY를 설정하세요.", file=sys.stderr)
        return 2

    store: ScannerStore | None = None
    try:
        if not args.no_state:
            store = ScannerStore(Path(args.state_db))
        client = DartClient(
            api_key=api_key,
            timeout_seconds=args.timeout_seconds,
        )
        pipeline = TransformationPipeline(
            client=client,
            store=store,
            include_documents=args.include_documents,
            cluster_lookback_days=args.cluster_lookback_days,
        )
        result = pipeline.scan(
            start_date=args.start,
            end_date=args.end,
            corp_classes=_csv_values(args.corp_classes),
            public_types=_csv_values(args.public_types),
            include_seen=args.include_seen,
            persist=not args.no_persist,
        )
    except (DartClientError, OSError, ValueError) as exc:
        print(f"탐색기 실행 실패: {exc}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()

    minimum = AlertBand(args.min_band)
    selected = tuple(
        candidate
        for candidate in result.candidates
        if _BAND_RANK[candidate.band] >= _BAND_RANK[minimum]
    )
    timestamp = _today_kst().strftime("%Y%m%d_%H%M%S")
    run_id = f"{args.start}_{args.end}_{timestamp}"
    jsonl_path, markdown_path = write_outputs(
        result=result,
        candidates=selected,
        output_dir=args.output_dir,
        run_id=run_id,
    )
    print(
        " | ".join(
            (
                f"조회={result.total_disclosures}",
                f"분류={result.classified_disclosures}",
                f"신규후보={len(result.candidates)}",
                f"리포트={len(selected)}",
                f"seen제외={result.skipped_seen}",
            )
        )
    )
    print(f"JSONL: {jsonl_path}")
    print(f"Markdown: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
