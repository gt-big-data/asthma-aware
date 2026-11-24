"""Read published NPZ metadata with range requests; never load full risk rasters."""
import argparse
import io
import json
import shutil
import subprocess
from uuid import UUID
from zipfile import ZipFile

import numpy as np
from google.cloud import storage
from google.oauth2.credentials import Credentials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline", required=True)
    parser.add_argument("--reference-sha", required=True)
    parser.add_argument("--require-history-tag", action="store_true")
    parser.add_argument("--bucket", default="asthmaaware-forecasts")
    parser.add_argument("--account", default="asthmaaware26@gmail.com")
    parser.add_argument("--project", default="asthma-aware")
    args = parser.parse_args()
    token = subprocess.run([shutil.which("gcloud"), "auth", "print-access-token", f"--account={args.account}"],
                           check=True, capture_output=True, text=True).stdout.strip()
    bucket = storage.Client(project=args.project, credentials=Credentials(token)).bucket(args.bucket)
    pointer = json.loads(bucket.blob("published/current.json").download_as_bytes(timeout=10))
    publication = str(UUID(pointer["publication_id"]))
    forecast = json.loads(bucket.blob(f"published/{publication}/forecast.json").download_as_bytes(timeout=10))
    for prefix, product in (("published", "forecast"), ("history", "analysis")):
        blob = bucket.blob(f"{prefix}/{publication}/risk.npz")
        if prefix == "history" and args.require_history_tag:
            blob.reload(timeout=10)
            assert (blob.metadata or {}).get("preprocessing") == args.pipeline
        with blob.open("rb", chunk_size=256 * 1024) as remote:
            with ZipFile(remote) as archive:
                metadata = json.loads(str(np.load(io.BytesIO(archive.read("metadata.npy")), allow_pickle=False)))
        assert metadata["preprocessing"] == args.pipeline, prefix
        assert metadata["reference_id"] == args.reference_sha, prefix
        assert metadata["product"] == product, prefix
        if product == "forecast":
            assert metadata["source_run"] == forecast["source_run"]
    print(json.dumps({"publication_id": publication, "source_run": forecast["source_run"],
        "pipeline": args.pipeline, "reference_sha256": args.reference_sha,
        "forecast_and_history_match": True}, indent=2))


if __name__ == "__main__":
    main()
