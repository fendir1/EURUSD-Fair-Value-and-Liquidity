"""Audited full-source ingestion and portable canonical quote collections."""

import hashlib
import json
import os
import tempfile
from importlib.metadata import version
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from . import features as io
from .data import _sha256, _writer_lock, ingest_quotes


def _utc(value):
    value = pd.Timestamp(value)
    if pd.isna(value) or value.tzinfo is None:
        raise ValueError("A valid timezone-aware timestamp is required.")
    return value.tz_convert("UTC")


def _relative(root, path):
    path = Path(path).resolve()
    if not path.is_relative_to(root):
        raise RuntimeError("An artifact path escapes the repository.")
    return path.relative_to(root).as_posix()


def validate_audits(audits):
    if not audits:
        raise ValueError("At least one completed source audit is required.")
    ordered = sorted(audits, key=lambda item: _utc(item["utc_min"]))
    if len({item["source_sha256"] for item in ordered}) != len(ordered):
        raise ValueError("Duplicate complete source content requires review.")

    previous_end = None
    for item in ordered:
        if (
            item["status"] != "complete"
            or item["rows_scanned"] <= 0
            or item["valid_timestamp_rows"] != item["rows_scanned"]
            or item["unassigned_timestamp_rows"] != 0
            or item["source_order_backsteps"] != 0
            or item["repeated_valid_timestamp_steps"] != 0
            or item["adjacent_exact_export_duplicate_rows"] != 0
        ):
            raise ValueError("Source timestamps or ordering require review.")
        start, end = _utc(item["utc_min"]), _utc(item["utc_max"])
        if (
            start > end
            or item["first_record"]["source_row"] != 1
            or item["last_record"]["source_row"] != item["rows_scanned"]
            or _utc(item["first_record"]["timestamp_utc"]) != start
            or _utc(item["last_record"]["timestamp_utc"]) != end
        ):
            raise ValueError("Audited source endpoints are inconsistent.")
        if previous_end is not None and start <= previous_end:
            raise ValueError("Source ranges overlap or share a timestamp; review required.")
        previous_end = end
    return ordered


def ingest_audited_source(raw_root, repository_root, audit, parameters):
    root = Path(repository_root).resolve()
    raw_root = Path(raw_root).resolve()
    source = (raw_root / audit["source_name"]).resolve()
    if not source.is_relative_to(raw_root) or not source.is_file():
        raise FileNotFoundError(source)
    if (
        source.stat().st_size != audit["identity"]["source_bytes"]
        or _sha256(source) != audit["source_sha256"]
    ):
        raise RuntimeError("Source content differs from the audited snapshot.")

    start = _utc(audit["utc_min"]).normalize()
    end = _utc(audit["utc_max"]).normalize() + pd.Timedelta(days=1)
    state = ingest_quotes(
        source,
        root / "data/interim/ingestion",
        root / "artifacts/manifests/ingestion",
        start, end,
        **parameters,
    )
    if (
        state["identity"]["source_sha256"] != audit["source_sha256"]
        or state["rows_scanned"] != audit["rows_scanned"]
        or state["rows_in_scope"] != audit["valid_timestamp_rows"]
        or state["rows_unassigned"] != 0
        or state["rows_outside_scope"] != 0
        or _utc(state["scope_utc_min"]) != _utc(audit["utc_min"])
        or _utc(state["scope_utc_max"]) != _utc(audit["utc_max"])
        or state["source_flags"] != audit["flags"]
    ):
        raise RuntimeError("Full-source ingestion differs from its audited results.")
    return state


def _bounds(parquet):
    field = parquet.schema_arrow.field("timestamp_utc")
    if not pa.types.is_timestamp(field.type) or field.type.tz != "UTC":
        raise RuntimeError("Canonical quote timestamps must explicitly carry UTC.")
    column = parquet.schema_arrow.get_field_index("timestamp_utc")
    minima, maxima = [], []
    for number in range(parquet.metadata.num_row_groups):
        stats = parquet.metadata.row_group(number).column(column).statistics
        if stats is None or not stats.has_min_max:
            result = pc.min_max(
                parquet.read(columns=["timestamp_utc"])["timestamp_utc"]
            ).as_py()
            minima, maxima = [result["min"]], [result["max"]]
            break
        minima.append(stats.min)
        maxima.append(stats.max)

    def convert(value):
        value = pd.Timestamp(value)
        # The field's verified Arrow type establishes UTC for naive statistics.
        return value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC")

    return (
        min(convert(value) for value in minima).isoformat(),
        max(convert(value) for value in maxima).isoformat(),
    )


def _boundary_overlay(original_path, target, first_timestamp, previous_last, gap_limit):
    original_path, target = Path(original_path).resolve(), Path(target).resolve()
    if original_path == target:
        raise RuntimeError("A boundary overlay cannot replace an original partition.")
    original_hash = _sha256(original_path)
    original = pq.ParquetFile(original_path).read()
    timestamp_type = original.schema.field("timestamp_utc").type
    mask = pc.equal(
        original["timestamp_utc"],
        pa.scalar(_utc(first_timestamp).to_pydatetime(), type=timestamp_type),
    )
    if pc.sum(pc.cast(mask, pa.int64())).as_py() != 1:
        raise RuntimeError("The first source quote is not unique in its first partition.")
    old_gap = pc.filter(original["source_gap_seconds"], mask)[0].as_py()
    if old_gap is not None and not pd.isna(old_gap):
        raise RuntimeError("The native first-source gap was unexpectedly populated.")

    gap = float((_utc(first_timestamp) - _utc(previous_last)).total_seconds())
    if gap <= 0:
        raise RuntimeError("A source boundary is not strictly chronological.")

    patched = original
    replacements = {
        "source_gap_seconds": gap,
        "flag_source_gap": gap > gap_limit,
    }
    for name, value in replacements.items():
        field = original.schema.field(name)
        replacement = pc.if_else(
            mask, pa.scalar(value, type=field.type), original[name]
        )
        patched = patched.set_column(
            patched.schema.get_field_index(name), field, replacement
        )

    for name in original.column_names:
        if name not in replacements:
            if not original[name].equals(patched[name]):
                raise RuntimeError("A boundary overlay altered an unrelated field.")
        elif not pc.filter(original[name], pc.invert(mask)).equals(
            pc.filter(patched[name], pc.invert(mask))
        ):
            raise RuntimeError("A boundary overlay altered another observation.")

    if io._schema_hash(original.schema) != io._schema_hash(patched.schema):
        raise RuntimeError("Boundary overlay schema changed.")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".boundary-", suffix=".tmp",
            dir=target.parent, delete=False,
        ) as handle:
            temporary = Path(handle.name)
            pq.write_table(patched, handle, compression="zstd")
            handle.flush()
            os.fsync(handle.fileno())
        io._replace_path(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    if _sha256(original_path) != original_hash:
        raise RuntimeError("An original source partition changed.")
    return {
        "rows": patched.num_rows,
        "bytes": target.stat().st_size,
        "sha256": _sha256(target),
        "schema_sha256": io._schema_hash(patched.schema),
    }



def verify_collection(repository_root, collection, verify_hashes=True):
    root = Path(repository_root).resolve()
    total = 0
    common_schema = None
    previous_end = None
    required = set(io.QUOTE_COLUMNS + ["flag_source_gap"])

    for member in collection["members"]:
        path = (root / member["ingestion_manifest"]).resolve()
        if (
            not path.is_relative_to(root) or not path.is_file()
            or _sha256(path) != member["ingestion_manifest_sha256"]
        ):
            raise RuntimeError("A source ingestion manifest changed.")

    for part in collection["parts"]:
        path = (root / part["path"]).resolve()
        if (
            part["kind"] != "quotes"
            or not path.is_relative_to(root) or not path.is_file()
            or path.stat().st_size != part["bytes"]
        ):
            raise RuntimeError("Canonical partition location or size failed.")
        if verify_hashes and _sha256(path) != part["sha256"]:
            raise RuntimeError("Canonical partition checksum failed.")
        parquet = pq.ParquetFile(path)
        schema = parquet.schema_arrow
        schema_hash = io._schema_hash(schema)
        if not required.issubset(schema.names):
            raise RuntimeError("A canonical partition lacks required fields.")
        if common_schema is None:
            common_schema = schema_hash
        if schema_hash != common_schema or parquet.metadata.num_rows != part["rows"]:
            raise RuntimeError("Canonical schemas or row counts differ.")
        if _bounds(parquet) != (part["min_utc"], part["max_utc"]):
            raise RuntimeError("Canonical partition timestamp bounds differ.")
        if previous_end is not None and _utc(part["min_utc"]) <= previous_end:
            raise RuntimeError("Canonical partition time ranges are not disjoint.")
        previous_end = _utc(part["max_utc"])
        total += part["rows"]

    if total != collection["rows_in_scope"] or total != collection["rows_scanned"]:
        raise RuntimeError("Canonical collection row conservation failed.")
    if not collection["parts"] or collection["rows_unassigned"] != 0:
        raise RuntimeError("The collection is empty or has unresolved timestamps.")
    if (
        _utc(collection["parts"][0]["min_utc"]) != _utc(collection["scope_utc_min"])
        or _utc(collection["parts"][-1]["max_utc"]) != _utc(collection["scope_utc_max"])
    ):
        raise RuntimeError("Canonical collection coverage differs.")


def build_collection(repository_root, audits, source_states, parameters,
                     audit_collection_path):
    root = Path(repository_root).resolve()
    audits = validate_audits(audits)
    by_hash = {
        state["identity"]["source_sha256"]: state for state in source_states
    }
    if len(by_hash) != len(audits) or set(by_hash) != {
        audit["source_sha256"] for audit in audits
    }:
        raise RuntimeError("Expected one complete ingestion per audited source.")

    members = []
    for rank, audit in enumerate(audits):
        state = by_hash[audit["source_sha256"]]
        if (
            state["status"] != "complete"
            or state["rows_in_scope"] != audit["rows_scanned"]
            or state["rows_unassigned"] != 0
            or state["rows_outside_scope"] != 0
            or state["source_flags"] != audit["flags"]
        ):
            raise RuntimeError("A source generation does not match its audit.")
        previous = None if rank == 0 else audits[rank - 1]["utc_max"]
        gap = None if previous is None else float(
            (_utc(audit["utc_min"]) - _utc(previous)).total_seconds()
        )
        members.append({
            "source_rank": rank,
            "source_file": audit["source_name"],
            "source_sha256": audit["source_sha256"],
            "rows": audit["rows_scanned"],
            "utc_min": audit["utc_min"], "utc_max": audit["utc_max"],
            "previous_source_last_utc": previous,
            "boundary_gap_seconds": gap,
            "ingestion_manifest": _relative(root, state["manifest_path"]),
            "ingestion_manifest_sha256": _sha256(state["manifest_path"]),
        })

    identity = {
        "audit_collection": _relative(root, audit_collection_path),
        "audit_collection_sha256": _sha256(audit_collection_path),
        "sources": members,
        "parameters": parameters,
        "start_utc": _utc(audits[0]["utc_min"]).normalize().isoformat(),
        "end_utc": (
            _utc(audits[-1]["utc_max"]).normalize() + pd.Timedelta(days=1)
        ).isoformat(),
        "boundary_policy": "first later-source quote uses preceding source endpoint",
        "implementation_sha256": hashlib.sha256(b"".join(
            Path(__file__).with_name(name).read_bytes()
            for name in ("history.py", "data.py", "quality.py", "features.py")
        )).hexdigest(),
        "versions": {
            name: version(name) for name in ("pandas", "pyarrow", "duckdb")
        },
    }
    job_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode()
    ).hexdigest()[:24]
    manifest_root = root / "artifacts/manifests/history"
    manifest = manifest_root / f"collection-{job_id}.json"

    with _writer_lock(manifest_root):
        if manifest.exists():
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            if saved["identity"] != identity:
                raise RuntimeError("Existing canonical collection identity differs.")
            verify_collection(root, saved)
            return {**saved, "cache_hit": True}

        parts, patches, flags = [], [], {}
        for member, audit in zip(members, audits):
            state = by_hash[member["source_sha256"]]
            source_parts = [
                part for part in state["parts"] if part["kind"] == "quotes"
            ]
            for name, count in state["source_flags"].items():
                flags[name] = flags.get(name, 0) + int(count)

            for number, native in enumerate(source_parts):
                original_path = Path(state["output_dir"]) / native["path"]
                selected_path = original_path
                record = dict(native)

                if number == 0 and member["source_rank"] > 0:
                    first = _utc(member["utc_min"])
                    selected_path = (
                        root / "data/interim/history" / job_id
                        / "boundary_overlays"
                        / f"year={first.year:04d}/month={first.month:02d}"
                        / f"source-{member['source_rank']:03d}.parquet"
                    )
                    metadata = _boundary_overlay(
                        original_path, selected_path, member["utc_min"],
                        member["previous_source_last_utc"],
                        parameters["gap_seconds"],
                    )
                    if metadata["rows"] != native["rows"]:
                        raise RuntimeError("Boundary replacement changed row count.")
                    record.update(metadata)
                    patches.append({
                        "source_file": member["source_file"],
                        "source_sha256": member["source_sha256"],
                        "first_timestamp_utc": member["utc_min"],
                        "previous_source_last_utc":
                            member["previous_source_last_utc"],
                        "gap_seconds": member["boundary_gap_seconds"],
                        "flag_source_gap": (
                            member["boundary_gap_seconds"] > parameters["gap_seconds"]
                        ),
                        "original_partition": _relative(root, original_path),
                        "original_partition_sha256": native["sha256"],
                        "overlay_partition": _relative(root, selected_path),
                    })
                    flags["flag_source_gap"] += int(
                        member["boundary_gap_seconds"] > parameters["gap_seconds"]
                    )

                parquet = pq.ParquetFile(selected_path)
                minimum, maximum = _bounds(parquet)
                record.update({
                    "path": _relative(root, selected_path),
                    "kind": "quotes",
                    "source_rank": member["source_rank"],
                    "min_utc": minimum, "max_utc": maximum,
                })
                parts.append(record)

        parts.sort(key=lambda part: _utc(part["min_utc"]))
        total = sum(audit["rows_scanned"] for audit in audits)
        collection = {
            "kind": "history_collection", "status": "complete",
            "job_id": job_id, "identity": identity,
            "members": members, "parts": parts,
            "boundary_patches": patches,
            "rows_scanned": total, "rows_in_scope": total,
            "rows_unassigned": 0, "rows_outside_scope": 0,
            "source_utc_min": audits[0]["utc_min"],
            "source_utc_max": audits[-1]["utc_max"],
            "scope_utc_min": audits[0]["utc_min"],
            "scope_utc_max": audits[-1]["utc_max"],
            "source_flags": flags, "scope_flags": dict(flags),
            "manifest_path": _relative(root, manifest),
        }
        verify_collection(root, collection)
        io.write_json(manifest, collection)
        return {**collection, "cache_hit": False}


def load_collection(repository_root, manifest_path, verify=True):
    root = Path(repository_root).resolve()
    path = Path(manifest_path)
    path = path.resolve() if path.is_absolute() else (root / path).resolve()
    collection = json.loads(path.read_text(encoding="utf-8"))
    if collection["kind"] != "history_collection" or collection["status"] != "complete":
        raise ValueError("A completed history collection is required.")
    if verify:
        verify_collection(root, collection)
    return {
        **collection,
        "output_dir": str(root),
        "manifest_path": str(path),
    }


def quote_files(repository_root, collection, start=None, end=None,
                include_predecessor=True):
    root = Path(repository_root).resolve()
    start = None if start is None else _utc(start)
    end = None if end is None else _utc(end)
    if start is not None and end is not None and start >= end:
        raise ValueError("Invalid quote-file selection range.")

    selected, predecessor = [], None
    for part in collection["parts"]:
        if start is not None and _utc(part["max_utc"]) < start:
            predecessor = part
            continue
        if end is not None and _utc(part["min_utc"]) >= end:
            continue
        selected.append(part)

    if start is not None and include_predecessor and predecessor is not None:
        selected.insert(0, predecessor)
    return [str(root / part["path"]) for part in selected]


def create_local_binding(repository_root, manifest_path, verify=True):
    root = Path(repository_root).resolve()
    collection = load_collection(root, manifest_path, verify=verify)
    collection_path = Path(collection["manifest_path"])
    collection_hash = _sha256(collection_path)
    binding_id = hashlib.sha256(
        f"{collection_hash}|{root}".encode()
    ).hexdigest()[:24]
    path = root / "artifacts/manifests/history_bindings" / f"{binding_id}.json"

    payload = {
        **collection,
        "kind": "history_collection_binding",
        "manifest_path": str(path),
        "canonical_collection_manifest": _relative(root, collection_path),
        "canonical_collection_sha256": collection_hash,
        "identity": {
            **collection["identity"],
            "canonical_collection_sha256": collection_hash,
        },
    }
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise RuntimeError("Existing local history binding differs.")
    else:
        io.write_json(path, payload)
    return path
