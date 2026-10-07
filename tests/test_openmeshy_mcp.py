"""openmeshy_mcp: the HTTP client helpers and the tool wiring, without a running lab."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp")

from openmeshy_mcp import client, server


def test_multipart_body_has_fields_and_file(tmp_path):
    image = tmp_path / "hero.png"
    image.write_bytes(b"PNGDATA")
    body, kind = client.encode_multipart({"settings": '{"a": 1}'}, {"image": image})
    boundary = kind.split("boundary=")[1]
    assert body.startswith(f"--{boundary}".encode()) and body.endswith(f"--{boundary}--\r\n".encode())
    assert b'name="settings"' in body and b'{"a": 1}' in body
    assert b'filename="hero.png"' in body and b"Content-Type: image/png" in body and b"PNGDATA" in body


def test_existing_file_checks_type(tmp_path):
    (tmp_path / "m.glb").write_bytes(b"x")
    assert client.existing_file(str(tmp_path / "m.glb"), {".glb"}) == tmp_path / "m.glb"
    with pytest.raises(ValueError):
        client.existing_file(str(tmp_path / "m.glb"), {".png"})
    with pytest.raises(ValueError):
        client.existing_file(str(tmp_path / "missing.png"))


def test_summarise_keeps_what_matters():
    out = client.summarise({"status": "done", "directory": "d", "last_event": {
        "phase": "done", "message": "Ready", "overall_pct": 100, "rig": {"ok": True},
        "zip_url": "/z", "noise": 1}})
    assert out == {"status": "done", "message": "Ready", "progress_pct": 100,
                   "rig": {"ok": True}, "zip_url": "/z", "directory": "d"}
    failed = client.summarise({"status": "error", "log_tail": "boom", "last_event": {}})
    assert failed["log_tail"] == "boom"


class FakeLab:
    base_url = "http://lab"

    def __init__(self):
        self.posts = []

    def ensure_lab(self):
        pass

    def post_json(self, path, body):
        self.posts.append((path, body))
        return {"job_id": "a" * 32}

    def post_form(self, path, fields, files):
        self.posts.append((path, json.loads(fields["settings"]), {k: v.name for k, v in files.items()}))
        return {"job_id": "b" * 32}

    def wait(self, kind, job_id, seconds):
        return {"status": "done", "last_event": {"message": "ok", "result_url": "/r.glb"}}

    def download(self, url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"glb")
        return dest

    def get(self, path):
        return {"backends": [{"id": "pixal3d", "state": "ready", "kind": "3d"},
                             {"id": "hunyuan-cuda", "state": "unsupported"}]}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    lab = FakeLab()
    monkeypatch.setattr(server, "lab", lab)
    monkeypatch.setattr(server, "REPO", tmp_path)
    return lab


def test_image_to_unity_posts_settings_and_returns_job(fake, tmp_path):
    image = tmp_path / "hero.png"
    image.write_bytes(b"x")
    result = server.image_to_unity(str(image), rig_class="quadruped", faces=10000)
    path, settings, files = fake.posts[0]
    assert path == "/api/unity" and settings["class"] == "quadruped" and settings["faces"] == 10000
    assert files == {"image": "hero.png"}
    assert result["job_id"] == "b" * 32 and "job_status('unity'" in result["next"]


def test_tools_post_json_and_wait(fake):
    result = server.autorig("output/x.glb", rig_class="humanoid", wait_seconds=10)
    assert fake.posts[0] == ("/api/tools/autorig", {"model": "output/x.glb", "class": "humanoid", "seed": 0})
    assert result["status"] == "done"
    server.export_unity("m.glb", rig_class="none", lods=["l1.glb"], unity_project="/p")
    assert fake.posts[1][1]["lods"] == ["l1.glb"] and fake.posts[1][1]["unity_project"] == "/p"


def test_job_status_downloads_finished_glb(fake, tmp_path):
    result = server.job_status("generate", "c" * 32)
    assert result["local_path"].endswith("result.glb")
    assert server.job_status("bogus", "x")["error"]


def test_bad_input_is_an_error_not_a_crash(fake, tmp_path):
    assert "error" in server.image_to_unity(str(tmp_path / "missing.png"))
    assert "error" in server.render_preview(str(tmp_path / "missing.glb"))


def test_lab_status_hides_unsupported(fake):
    ids = [b["id"] for b in server.lab_status()["backends"]]
    assert ids == ["pixal3d"]


def test_recent_runs(tmp_path):
    run = tmp_path / "unity" / "hero__unity__1"
    (run / "unity" / "hero").mkdir(parents=True)
    (run / "unity" / "hero" / "hero.fbx").write_text("x")
    (run / "unity" / "hero.zip").write_text("x")
    runs = server.recent_runs(tmp_path, 5)
    assert runs[0]["kind"] == "unity" and len(runs[0]["files"]) == 2
