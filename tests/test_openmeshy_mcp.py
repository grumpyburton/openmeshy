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
        settings = json.loads(fields["settings"]) if "settings" in fields else None
        self.posts.append((path, settings, {k: v.name for k, v in files.items()}))
        return {"job_id": "b" * 32}

    def wait(self, kind, job_id, seconds):
        if kind == "image":  # the image route answers with job.describe(), no last_event
            return {"status": "done", "result_url": "/api/image/x/result.png",
                    "path": "/out/images/goblin.png", "error": None}
        return {"status": "done", "last_event": {"message": "ok", "result_url": "/r.glb"}}

    def download(self, url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"glb")
        return dest

    def get(self, path):
        if path == "/api/backends":  # the Generate tab's routes, not the catalogue
            return {"backends": BACKENDS}
        return {"backends": [{"id": "pixal3d", "state": "ready", "kind": "3d"},
                             {"id": "hunyuan-cuda", "state": "unsupported"}]}


BACKENDS = [
    {"id": "pixal3d", "runs_here": True, "default_settings": {"seed": 42}},
    {"id": "trellis", "runs_here": True, "default_settings": {"seed": 0, "resolution": "1024"}},
    {"id": "sf3d", "runs_here": True, "default_settings": {"texture_resolution": 1024}},
    {"id": "hunyuan-cuda", "runs_here": False, "default_settings": {"seed": 42}},
]


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


def test_tools_post_json_and_wait(fake, tmp_path, monkeypatch):
    monkeypatch.setattr("image_to_3dlab.data_root.data_root", lambda: tmp_path)
    model, lod = tmp_path / "x.glb", tmp_path / "l1.glb"
    model.write_bytes(b"g")
    lod.write_bytes(b"g")
    result = server.autorig(str(model), rig_class="humanoid", wait_seconds=10)
    assert fake.posts[0] == ("/api/tools/autorig", {"model": str(model), "class": "humanoid", "seed": 0})
    assert result["status"] == "done"
    server.export_unity(str(model), rig_class="none", lods=[str(lod)], unity_project="/p")
    assert fake.posts[1][1]["lods"] == [str(lod)] and fake.posts[1][1]["unity_project"] == "/p"
    assert "error" in server.autorig(str(tmp_path / "missing.glb"))


def test_job_status_downloads_finished_glb(fake, tmp_path):
    result = server.job_status("generate", "c" * 32)
    assert result["local_path"].endswith("result.glb")
    assert server.job_status("bogus", "x")["error"]


def test_generate_image_posts_prompt_and_settings(fake):
    result = server.generate_image("a goblin in a T-pose", width=512, height=768, seed=7)
    path, body = fake.posts[0]
    assert path == "/api/image" and body["prompt"] == "a goblin in a T-pose"
    assert body["settings"] == {"width": 512, "height": 768, "seed": 7, "steps": 10,
                                "negative_prompt": ""}
    assert result["kind"] == "image" and "job_status('image'" in result["next"]
    assert "error" in server.generate_image("   ")


def test_finished_image_reports_its_saved_png(fake):
    result = server.generate_image("goblin", wait_seconds=60)
    assert result["status"] == "done" and result["local_path"] == "/out/images/goblin.png"
    assert server.job_status("image", "d" * 32)["local_path"] == "/out/images/goblin.png"


def test_summarise_reads_top_level_fields_without_an_event():
    out = client.summarise({"status": "error", "error": "sd-cli exited with code 1",
                            "result_url": None})
    assert out["error"] == "sd-cli exited with code 1" and "result_url" not in out


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


def test_relative_paths_prefer_the_callers_project(tmp_path, monkeypatch):
    (tmp_path / "art").mkdir()
    (tmp_path / "art" / "hero.png").write_bytes(b"x")
    assert client.resolve_path("art/hero.png", cwd=tmp_path) == tmp_path / "art" / "hero.png"
    assert client.resolve_path("scripts/autorig.py", cwd=tmp_path) == client.REPO / "scripts" / "autorig.py"
    assert client.resolve_path("/abs/x.glb", cwd=tmp_path) == client.Path("/abs/x.glb")


def test_foreign_files_are_staged_inside(tmp_path):
    inside_root, outside = tmp_path / "repo", tmp_path / "game"
    (inside_root / "output").mkdir(parents=True)
    outside.mkdir()
    own = inside_root / "output" / "a.glb"
    own.write_bytes(b"a")
    theirs = outside / "b.glb"
    theirs.write_bytes(b"b")
    staging = inside_root / "output" / "mcp" / "inputs"
    assert client.stage_input(own, staging, (inside_root,)) == own
    staged = client.stage_input(theirs, staging, (inside_root,))
    assert staging in staged.parents and staged.read_bytes() == b"b"


def test_install_to_unity(fake, tmp_path):
    folder = tmp_path / "hero"
    folder.mkdir()
    (folder / "hero.openmeshy.json").write_text("{}")
    project = tmp_path / "Game"
    (project / "Assets").mkdir(parents=True)
    (project / "ProjectSettings").mkdir()
    result = server.install_to_unity(str(folder), str(project))
    assert result["installed"].endswith("Assets/OpenMeshy/hero")
    assert "error" in server.install_to_unity(str(tmp_path), str(project))


def test_generate_3d_defaults_to_pixal3d(fake, tmp_path):
    image = tmp_path / "chest.png"
    image.write_bytes(b"x")
    server.generate_3d(str(image), seed=7)
    assert fake.posts[0] == ("/api/generate", {"backend": "pixal3d", "seed": 7},
                             {"image": "chest.png"})


def test_generate_3d_can_pick_another_backend(fake, tmp_path):
    image = tmp_path / "chest.png"
    image.write_bytes(b"x")
    server.generate_3d(str(image), backend="trellis", seed=3)
    assert fake.posts[0][1] == {"backend": "trellis", "seed": 3}
    # SF3D takes no seed, and the lab rejects settings a route does not know.
    server.generate_3d(str(image), backend="sf3d")
    assert fake.posts[1][1] == {"backend": "sf3d"}


def test_generate_3d_refuses_a_backend_this_machine_cannot_run(fake, tmp_path):
    image = tmp_path / "chest.png"
    image.write_bytes(b"x")
    for name in ("hunyuan-cuda", "nonsense"):
        error = server.generate_3d(str(image), backend=name)["error"]
        assert "does not run on this machine" in error and "pixal3d" in error
        assert "hunyuan-cuda," not in error  # only routes that run here are offered
    assert fake.posts == []


def test_rebind_rig_sends_the_three_rig_review_files(fake, tmp_path):
    model, scene, sidecar = (tmp_path / "hero.glb", tmp_path / "hero.blend",
                             tmp_path / "hero.rig.json")
    for f in (model, scene, sidecar):
        f.write_bytes(b"x")
    result = server.rebind_rig(str(model), str(scene), str(sidecar))
    assert fake.posts[0] == ("/api/rig/rebind", None, {
        "asset": "hero.glb", "scene": "hero.blend", "sidecar": "hero.rig.json"})
    assert result["kind"] == "rig" and result["status"] == "done"


def test_rebind_rig_wants_a_rig_sidecar(fake, tmp_path):
    model, scene, other = tmp_path / "h.glb", tmp_path / "h.blend", tmp_path / "h.json"
    for f in (model, scene, other):
        f.write_bytes(b"x")
    assert "rig.json" in server.rebind_rig(str(model), str(scene), str(other))["error"]
    assert fake.posts == []


def test_rig_jobs_have_a_status_route():
    assert client.KINDS["rig"] == "rig/rebind"
    assert "rig" in (server.job_status.__doc__ or "")


def test_image_tool_does_not_promise_a_mac():
    doc = server.generate_image.__doc__ or ""
    assert "on this Mac" not in doc and "~12 min." not in doc
