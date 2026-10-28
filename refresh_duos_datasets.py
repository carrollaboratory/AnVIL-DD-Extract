#!/usr/bin/env python3

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import google.auth
from google.auth.transport.requests import Request
import requests


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = ROOT_DIR / "data" / "dataset_results"
DEFAULT_API_URL = (
    "https://consent.dsde-prod.broadinstitute.org/api/dataset/search/index"
)
DEFAULT_PAGE_SIZE = 1000


def get_access_token() -> str:
    environment_token = os.environ.get("DUOS_ACCESS_TOKEN")
    if environment_token:
        return environment_token

    credentials, _ = google.auth.default()
    credentials.refresh(Request())
    if not credentials.token:
        raise RuntimeError("Application Default Credentials did not provide an access token.")
    return credentials.token


def fetch_all_datasets(
    api_url: str,
    access_token: str,
    page_size: int,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    if page_size < 1:
        raise ValueError("page_size must be at least 1")

    http = session or requests.Session()
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    datasets: List[Dict[str, Any]] = []

    while True:
        query = {
            "from": len(datasets),
            "size": page_size,
            "query": {"match_all": {}},
        }
        response = http.post(api_url, headers=headers, json=query, timeout=60)
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, list):
            raise ValueError(
                "Unexpected DUOS response: expected the dataset search endpoint "
                "to return a JSON array."
            )

        datasets.extend(page)
        if len(page) < page_size:
            return datasets


def write_datasets(
    datasets: List[Dict[str, Any]],
    output_dir: Path,
    date_suffix: Optional[str] = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = date_suffix or datetime.now().strftime("%Y%m%d")
    output_path = output_dir / f"datasets_{suffix}.json"
    temporary_path = output_path.with_suffix(".json.tmp")

    with temporary_path.open("w", encoding="utf-8") as output_file:
        json.dump(datasets, output_file, indent=2)
        output_file.write("\n")
    temporary_path.replace(output_path)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch all datasets from the DUOS dataset search index."
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help="DUOS dataset search endpoint.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for versioned dataset JSON files.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="Number of dataset records requested per API call.",
    )
    args = parser.parse_args()

    datasets = fetch_all_datasets(
        api_url=args.api_url,
        access_token=get_access_token(),
        page_size=args.page_size,
    )
    output_path = write_datasets(datasets, args.output_dir)
    print(f"Saved {len(datasets)} datasets to {output_path}")


if __name__ == "__main__":
    main()
