"""Lesson 8: send a file in, get the agent's files out.

1. Upload sample data (POST /v1/files, multipart).
2. Run a task that references it by file_id and asks for a report file.
3. Read the artifacts: from the response's annotations, and by listing the session's files.
4. Download them into ./out/

    python 06_files_and_artifacts.py --provider anthropic
"""
from pathlib import Path

import hr

a = hr.args(__doc__)
prof = hr.profile(a.provider)
hr.banner("Files and artifacts", prof)

csv = Path(__file__).parent / "sample_data" / "sales.csv"
out_dir = Path(__file__).parent / "out"
out_dir.mkdir(exist_ok=True)

with hr.http() as c:
    # 1. Upload. Small files can also go inline as a data: URL in `file_data`.
    with csv.open("rb") as f:
        up = hr.check(c.post("/v1/files", files={"file": (csv.name, f, "text/csv")},
                             data={"purpose": "user_data"}))
    print(f"uploaded {up['filename']} -> {up['id']} ({up.get('bytes')} bytes)")

    # 2. A task with an input_file item. The file lands in the agent's working directory.
    resp = hr.check(c.post("/v1/responses", json={
        "model": prof.model,
        "metadata": {"harness_id": prof.harness_id},
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text",
                 "text": "Read sales.csv. Write report.md with total revenue per region, sorted "
                         "high to low, and one sentence of insight. Reply with the top region only."},
                {"type": "input_file", "file_id": up["id"]},
            ],
        }],
    }))
    print("answer:", hr.output_text(resp))
    hr.describe(resp)

    # 3. List every file in the session's workspace (?changed=true for only the last turn's).
    sid = (resp.get("metadata") or {}).get("session_id")
    listing = hr.check(c.get(f"/v1/sessions/{sid}/files", params={"changed": "true"}))
    files = listing.get("files") or []
    print(f"\nfiles changed in the last turn: {len(files)}")

    # 4. Download. container_id is the session; bytes come back raw, not JSON.
    for f in files:
        fid = f.get("file_id") or f.get("id")
        cid = f.get("container_id") or sid
        name = f.get("path") or f.get("filename")
        data = c.get(f"/v1/containers/{cid}/files/{fid}/content")
        if data.status_code == 200:
            dest = out_dir / Path(name).name
            dest.write_bytes(data.content)
            print(f"  saved {name} -> {dest}")

    print(f"\nall files as one zip: GET {hr.base_url()}/v1/sessions/{sid}/files/archive")
