#!/usr/bin/env python3
"""Generate standalone BigQuery export SQL from a TDR snapshot-list response."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NoReturn


DEFAULT_TDR_BASE_URL = "https://data.terra.bio/api/repository/v1"
PROJECT_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SNAPSHOT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")


def should_export_table(table_name: str) -> bool:
    """Keep the metadata table while excluding other anvil helper tables."""
    return table_name == "anvil_dataset" or not table_name.startswith("anvil_")


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    source_project: str
    source_dataset: str
    destination_template: str
    tables: tuple[str, ...] = ()


def fail(message: str) -> NoReturn:
    raise SystemExit(f"ERROR: {message}")


def validate_destination_root(destination_root: str) -> str:
    root = destination_root.strip().rstrip("/")
    if not root.startswith("gs://") or root == "gs://":
        fail("destination root must be a non-empty gs:// path")
    if "*" in root or any(character.isspace() for character in root) or "'" in root:
        fail("destination root cannot contain '*', whitespace, or single quotes")
    return root


def read_catalog(path: Path, destination_root: str) -> list[Snapshot]:
    try:
        with path.open(encoding="utf-8-sig") as handle:
            payload = json.load(handle)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"input is not a valid TDR JSON response: {error}")

    if not isinstance(payload, dict):
        fail("TDR JSON response must be an object containing an 'items' array")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        fail("TDR JSON response has no 'items' array")
    if not raw_items:
        fail("TDR JSON response contains no snapshot items")

    filtered_total = payload.get("filteredTotal")
    if (
        isinstance(filtered_total, int)
        and not isinstance(filtered_total, bool)
        and filtered_total > len(raw_items)
    ):
        fail(
            f"TDR response contains {len(raw_items)} item(s), but "
            f"filteredTotal is {filtered_total}; provide a response containing "
            "all filtered pages to avoid omitting snapshots"
        )

    root = validate_destination_root(destination_root)
    catalog: list[Snapshot] = []
    sources: set[tuple[str, str]] = set()
    destinations: set[str] = set()
    snapshot_ids: set[str] = set()

    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            fail(f"item {index}: snapshot entry is not an object")

        snapshot_id = item.get("id")
        project = item.get("dataProject")
        dataset = item.get("name")
        if not isinstance(snapshot_id, str) or not SNAPSHOT_ID_PATTERN.fullmatch(
            snapshot_id
        ):
            fail(f"item {index}: invalid TDR snapshot id: {snapshot_id!r}")
        if not isinstance(project, str) or not PROJECT_PATTERN.fullmatch(project):
            fail(f"item {index}: invalid BigQuery dataProject: {project!r}")
        if not isinstance(dataset, str) or not IDENTIFIER_PATTERN.fullmatch(dataset):
            fail(f"item {index}: invalid BigQuery dataset name: {dataset!r}")

        destination = f"{root}/{dataset}/*.parquet"
        source = (project, dataset)
        if snapshot_id in snapshot_ids:
            fail(f"item {index}: duplicate TDR snapshot ID: {snapshot_id}")
        if source in sources:
            fail(f"item {index}: duplicate BigQuery source: {project}.{dataset}")
        if destination in destinations:
            fail(f"item {index}: duplicate destination: {destination}")

        snapshot_ids.add(snapshot_id)
        sources.add(source)
        destinations.add(destination)
        catalog.append(Snapshot(snapshot_id, project, dataset, destination))

    return catalog


def get_access_token() -> str:
    environment_token = os.environ.get("TDR_ACCESS_TOKEN", "").strip()
    if environment_token:
        return environment_token

    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        fail(
            "gcloud is not installed; run in Terra or set TDR_ACCESS_TOKEN "
            "to an access token"
        )
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or "gcloud returned a nonzero status"
        fail(f"could not obtain a TDR access token: {detail}")

    token = result.stdout.strip()
    if not token:
        fail("gcloud auth print-access-token returned an empty token")
    return token


def api_error_message(error: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return error.reason

    if isinstance(payload, dict):
        for key in ("message", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return error.reason


def fetch_table_names(
    snapshot: Snapshot, token: str, tdr_base_url: str
) -> tuple[str, ...]:
    snapshot_path = urllib.parse.quote(snapshot.snapshot_id, safe="")
    url = f"{tdr_base_url.rstrip('/')}/snapshots/{snapshot_path}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        fail(
            f"snapshot {snapshot.snapshot_id}: TDR API returned HTTP "
            f"{error.code}: {api_error_message(error)}"
        )
    except urllib.error.URLError as error:
        fail(f"snapshot {snapshot.snapshot_id}: TDR API request failed: {error.reason}")
    except (TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"snapshot {snapshot.snapshot_id}: invalid TDR API response: {error}")

    if not isinstance(payload, dict):
        fail(f"snapshot {snapshot.snapshot_id}: TDR API response is not an object")

    raw_tables = payload.get("tables")
    if not isinstance(raw_tables, list):
        fail(f"snapshot {snapshot.snapshot_id}: TDR response has no tables list")

    tables: list[str] = []
    seen: set[str] = set()
    for index, raw_table in enumerate(raw_tables):
        if not isinstance(raw_table, dict):
            fail(
                f"snapshot {snapshot.snapshot_id}: table {index} is not an object"
            )
        table_name = raw_table.get("name")
        if not isinstance(table_name, str) or not IDENTIFIER_PATTERN.fullmatch(
            table_name
        ):
            fail(
                f"snapshot {snapshot.snapshot_id}: invalid table name "
                f"returned by TDR: {table_name!r}"
            )
        if table_name not in seen and should_export_table(table_name):
            seen.add(table_name)
            tables.append(table_name)

    if not tables:
        fail(
            f"snapshot {snapshot.snapshot_id}: TDR returned no tables after "
            "excluding anvil_ prefixed tables"
        )
    return tuple(tables)


def resolve_tables(
    catalog: list[Snapshot], token: str, tdr_base_url: str
) -> list[Snapshot]:
    resolved: list[Snapshot] = []
    for index, snapshot in enumerate(catalog, start=1):
        print(
            f"Resolving tables for snapshot {index}/{len(catalog)}: "
            f"{snapshot.snapshot_id}",
            file=sys.stderr,
        )
        tables = fetch_table_names(snapshot, token, tdr_base_url)
        print(f"  Found {len(tables)} table(s): {', '.join(tables)}", file=sys.stderr)
        resolved.append(replace(snapshot, tables=tables))
    return resolved


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def generate_sql(catalog: list[Snapshot]) -> str:
    catalog_rows = ",\n".join(
        "  STRUCT("
        f"{sql_string(snapshot.source_project)} AS source_project, "
        f"{sql_string(snapshot.source_dataset)} AS source_dataset, "
        f"{sql_string(snapshot.destination_template)} AS destination_template, "
        "["
        + ", ".join(sql_string(table) for table in snapshot.tables)
        + "] AS tables)"
        for snapshot in catalog
    )
    table_count = sum(len(snapshot.tables) for snapshot in catalog)

    return f"""-- Generated by generate_tdr_export_sql.py
-- {len(catalog)} TDR snapshot(s), {table_count} table export(s).
-- Table names were resolved from each snapshot's TDR API metadata.

DECLARE snapshot_catalog ARRAY<STRUCT<
  source_project STRING,
  source_dataset STRING,
  destination_template STRING,
  tables ARRAY<STRING>
>> DEFAULT [
{catalog_rows}
];

FOR snapshot IN (
  SELECT source_project, source_dataset, destination_template, tables
  FROM UNNEST(snapshot_catalog)
)
DO
  SELECT FORMAT(
    'Exporting %d table(s) from %s.%s',
    ARRAY_LENGTH(snapshot.tables),
    snapshot.source_project,
    snapshot.source_dataset
  ) AS export_status;

  FOR table_record IN (
    SELECT table_name
    FROM UNNEST(snapshot.tables) AS table_name
  )
  DO
    EXECUTE IMMEDIATE FORMAT(
      '''
      EXPORT DATA OPTIONS (
        uri = '%s',
        format = 'PARQUET',
        compression = 'SNAPPY',
        overwrite = true
      ) AS
      SELECT *
      FROM `%s.%s.%s`
      ''',
      REPLACE(
        snapshot.destination_template,
        '*',
        CONCAT(table_record.table_name, '-*')
      ),
      snapshot.source_project,
      snapshot.source_dataset,
      table_record.table_name
    );
  END FOR;
END FOR;
"""


def write_output(sql: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(sql)
        return

    output.write_text(sql, encoding="utf-8")
    print(f"Wrote BigQuery SQL to {output}", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read a TDR snapshot-list JSON response, resolve every snapshot "
            "table from TDR, and generate standalone BigQuery EXPORT DATA SQL."
        )
    )
    parser.add_argument(
        "input_file",
        type=Path,
        help="JSON file containing the TDR snapshot-list response with an items array",
    )
    parser.add_argument(
        "--destination-root",
        required=True,
        help=(
            "GCS destination root, for example "
            "gs://my-bucket/tdr-exports; each snapshot is written under "
            "<root>/<snapshot-name>/"
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="write SQL to this file instead of standard output",
    )
    parser.add_argument(
        "--tdr-base-url",
        default=os.environ.get("TDR_BASE_URL", DEFAULT_TDR_BASE_URL),
        help=f"TDR API base URL (default: {DEFAULT_TDR_BASE_URL})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.input_file.is_file():
        fail(f"input file does not exist: {args.input_file}")

    catalog = read_catalog(args.input_file, args.destination_root)
    resolved = resolve_tables(catalog, get_access_token(), args.tdr_base_url)
    write_output(generate_sql(resolved), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
