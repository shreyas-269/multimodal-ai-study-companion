import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

# Load transcribe_lectures dynamically to ensure module isolation
SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "transcribe_lectures.py"
spec = importlib.util.spec_from_file_location("transcribe_lectures", SCRIPT_PATH)
assert spec is not None
assert spec.loader is not None
tl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tl)


def test_lazy_imports_no_sys_modules():
    """Importing the script must put neither faster_whisper nor av into sys.modules."""
    for mod_name in ["faster_whisper", "av"]:
        sys.modules.pop(mod_name, None)

    spec_fresh = importlib.util.spec_from_file_location("transcribe_fresh", SCRIPT_PATH)
    assert spec_fresh is not None
    assert spec_fresh.loader is not None
    fresh_mod = importlib.util.module_from_spec(spec_fresh)
    spec_fresh.loader.exec_module(fresh_mod)

    assert "faster_whisper" not in sys.modules
    assert "av" not in sys.modules


def test_format_time():
    """Verify time formatting for mm:ss and h:mm:ss."""
    assert tl.format_time(0) == "00:00"
    assert tl.format_time(45.4) == "00:45"
    assert tl.format_time(3082.12) == "51:22"
    assert tl.format_time(3600) == "1:00:00"
    assert tl.format_time(3665) == "1:01:05"
    assert tl.format_time(125, force_hours=True) == "0:02:05"


def test_discover_lectures(tmp_path):
    """Lecture discovery sorts ^L\\d{2}\\.mp4 files and ignores others."""
    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()

    (videos_dir / "L02.mp4").touch()
    (videos_dir / "l01.mp4").touch()
    (videos_dir / "L03.MP4").touch()
    (videos_dir / "L02-copy.mp4").touch()
    (videos_dir / "video.mp4").touch()
    (videos_dir / "L1.mp4").touch()
    (videos_dir / "L100.mp4").touch()
    (videos_dir / "notes.txt").touch()

    lectures = tl.discover_lectures(videos_dir)
    assert list(lectures.keys()) == ["L01", "L02", "L03"]
    assert lectures["L01"].name == "l01.mp4"
    assert lectures["L02"].name == "L02.mp4"
    assert lectures["L03"].name == "L03.MP4"


def test_check_lecture_done(tmp_path):
    """Check lecture completion logic against valid, corrupt, and incomplete JSON."""
    json_path = tmp_path / "L01.json"

    # Missing
    done, model = tl.check_lecture_done(json_path)
    assert not done and model is None

    # Corrupt
    json_path.write_text("not json", encoding="utf-8")
    done, model = tl.check_lecture_done(json_path)
    assert not done and model is None

    # Invalid schema version
    bad_schema = {"schema_version": 2, "segments": [{"id": 0}]}
    json_path.write_text(json.dumps(bad_schema), encoding="utf-8")
    done, model = tl.check_lecture_done(json_path)
    assert not done and model is None

    # Clip trial (clip_seconds not null)
    clip_trial = {
        "schema_version": 1,
        "clip_seconds": 10.0,
        "segments": [{"id": 0}],
        "model": "medium.en",
    }
    json_path.write_text(json.dumps(clip_trial), encoding="utf-8")
    done, model = tl.check_lecture_done(json_path)
    assert not done and model is None

    # Empty segments
    empty_segs = {
        "schema_version": 1,
        "clip_seconds": None,
        "segments": [],
        "model": "medium.en",
    }
    json_path.write_text(json.dumps(empty_segs), encoding="utf-8")
    done, model = tl.check_lecture_done(json_path)
    assert not done and model is None

    # Valid done file
    valid_done = {
        "schema_version": 1,
        "clip_seconds": None,
        "segments": [{"id": 0, "text": "intro"}],
        "model": "medium.en",
    }
    json_path.write_text(json.dumps(valid_done), encoding="utf-8")
    done, model = tl.check_lecture_done(json_path)
    assert done is True
    assert model == "medium.en"

    # Leftover .tmp only
    json_path.unlink()
    tmp_file = tmp_path / "L01.json.tmp"
    tmp_file.write_text("{}", encoding="utf-8")
    done, model = tl.check_lecture_done(json_path)
    assert not done and model is None


def test_clean_segments():
    """Verify segment rounding, text stripping, empty segment filtering, and ID renumbering."""
    raw = [
        {
            "start": 0.1234,
            "end": 4.5678,
            "text": "  Hello probability  ",
            "avg_logprob": -0.12345,
            "no_speech_prob": 0.00123,
            "compression_ratio": 1.4567,
        },
        {
            "start": 4.5678,
            "end": 6.0,
            "text": "   ",  # Empty after stripping -> dropped
            "avg_logprob": -0.5,
            "no_speech_prob": 0.9,
            "compression_ratio": 2.0,
        },
        {
            "start": 6.0,
            "end": 10.556,
            "text": "Sample space Ω and π",
            "avg_logprob": -0.9876,
            "no_speech_prob": 0.0001,
            "compression_ratio": 1.1111,
        },
    ]

    cleaned = tl.clean_segments(raw)
    assert len(cleaned) == 2

    # First segment
    assert cleaned[0]["id"] == 0
    assert cleaned[0]["start"] == 0.12
    assert cleaned[0]["end"] == 4.57
    assert cleaned[0]["text"] == "Hello probability"
    assert cleaned[0]["avg_logprob"] == -0.123
    assert cleaned[0]["no_speech_prob"] == 0.001
    assert cleaned[0]["compression_ratio"] == 1.457

    # Second segment (renumbered from 0 to 1)
    assert cleaned[1]["id"] == 1
    assert cleaned[1]["start"] == 6.0
    assert cleaned[1]["end"] == 10.56
    assert cleaned[1]["text"] == "Sample space Ω and π"
    assert cleaned[1]["avg_logprob"] == -0.988
    assert cleaned[1]["no_speech_prob"] == 0.000
    assert cleaned[1]["compression_ratio"] == 1.111


def test_atomic_writes_and_replace(tmp_path):
    """Atomic write creates target without leftover .tmp and safely replaces existing file."""
    text_file = tmp_path / "L01.txt"
    json_file = tmp_path / "L01.json"

    # Initial text write
    tl.atomic_write_text(text_file, "[00:00] First version\n")
    assert text_file.read_text(encoding="utf-8") == "[00:00] First version\n"
    assert not (tmp_path / "L01.txt.tmp").exists()

    # Replace existing text file
    tl.atomic_write_text(text_file, "[00:00] Replaced version\n")
    assert text_file.read_text(encoding="utf-8") == "[00:00] Replaced version\n"
    assert not (tmp_path / "L01.txt.tmp").exists()

    # Initial JSON write
    tl.atomic_write_json(json_file, {"schema_version": 1, "v": 1})
    with open(json_file, encoding="utf-8") as f:
        data = json.load(f)
    assert data["v"] == 1
    assert not (tmp_path / "L01.json.tmp").exists()

    # Replace existing JSON file
    tl.atomic_write_json(json_file, {"schema_version": 1, "v": 2})
    with open(json_file, encoding="utf-8") as f:
        data = json.load(f)
    assert data["v"] == 2
    assert not (tmp_path / "L01.json.tmp").exists()


def test_timing_generator_consumption(tmp_path):
    """Timer starts before transcribe and stops only after consuming the generator."""
    video = tmp_path / "video.mp4"
    video.touch()

    class FakeModel:
        def transcribe(self, *args, **kwargs):
            def generator():
                for i in range(3):
                    time.sleep(0.02)
                    yield {"start": float(i), "end": float(i + 1), "text": f"seg {i}"}
            return generator(), MagicMock()

    model = FakeModel()
    cleaned, transcribe_secs, rtf = tl.transcribe_lecture(
        model=model,
        video_path=video,
        duration_s=100.0,
    )
    assert len(cleaned) == 3
    assert transcribe_secs >= 0.05
    assert rtf > 0.0


def test_run_pipeline_skip_and_force(tmp_path):
    """Pipeline skips completed lectures unless --force is passed; force redoes all."""
    course_dir = tmp_path / "course"
    videos_dir = course_dir / "videos"
    videos_dir.mkdir(parents=True)
    (videos_dir / "L01.mp4").touch()
    (videos_dir / "L02.mp4").touch()

    transcripts_dir = course_dir / "derived" / "transcripts"
    transcripts_dir.mkdir(parents=True)

    # Pre-mark L01 as done
    valid_payload = {
        "schema_version": 1,
        "lecture": "L01",
        "video_file": "videos/L01.mp4",
        "model": "medium.en",
        "compute_type": "int8",
        "threads": 4,
        "language": "en",
        "faster_whisper_version": "1.0",
        "settings": {
            "beam_size": 5,
            "vad_filter": True,
            "condition_on_previous_text": False,
            "initial_prompt": "",
        },
        "duration_s": 50.0,
        "clip_seconds": None,
        "transcribe_seconds": 5.0,
        "realtime_factor": 0.1,
        "created_at": "2026-10-07T00:00:00Z",
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 4.0,
                "text": "done before",
                "avg_logprob": -0.1,
                "no_speech_prob": 0.0,
                "compression_ratio": 1.0,
            }
        ],
    }
    (transcripts_dir / "L01.json").write_text(json.dumps(valid_payload), encoding="utf-8")
    (transcripts_dir / "L01.txt").write_text("[00:00] done before\n", encoding="utf-8")

    processed = []

    def fake_transcriber(model, video_path, duration_s, clip_seconds, on_progress):
        lid = video_path.stem.upper()
        processed.append(lid)
        return (
            [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 4.0,
                    "text": f"new {lid}",
                    "avg_logprob": -0.1,
                    "no_speech_prob": 0.0,
                    "compression_ratio": 1.0,
                }
            ],
            2.0,
            0.05,
        )

    # Run 1: L01 should be skipped, L02 processed
    args = argparse.Namespace(
        course_dir=str(course_dir),
        model="medium.en",
        compute_type="int8",
        threads=4,
        only=None,
        clip_seconds=None,
        force=False,
        dry_run=False,
    )
    code = tl.run_pipeline(
        args,
        duration_reader=lambda p: 60.0,
        model_loader=lambda m, c, t: (None, "1.0"),
        transcriber=fake_transcriber,
    )
    assert code == 0
    assert processed == ["L02"]

    # Run 2 with --force: redoes L01 and L02
    processed.clear()
    args.force = True
    code = tl.run_pipeline(
        args,
        duration_reader=lambda p: 60.0,
        model_loader=lambda m, c, t: (None, "1.0"),
        transcriber=fake_transcriber,
    )
    assert code == 0
    assert processed == ["L01", "L02"]


def test_force_deletes_json_before_processing(tmp_path):
    """Under --force, existing L??.json is deleted before processing so a crash leaves it todo."""
    course_dir = tmp_path / "course"
    videos_dir = course_dir / "videos"
    videos_dir.mkdir(parents=True)
    (videos_dir / "L01.mp4").touch()

    transcripts_dir = course_dir / "derived" / "transcripts"
    transcripts_dir.mkdir(parents=True)
    json_path = transcripts_dir / "L01.json"
    dummy_doc = {
        "schema_version": 1,
        "clip_seconds": None,
        "segments": [{"id": 0, "text": "old"}],
    }
    json_path.write_text(json.dumps(dummy_doc), encoding="utf-8")

    # Stale .tmp files present
    stale_txt_tmp = transcripts_dir / "L01.txt.tmp"
    stale_json_tmp = transcripts_dir / "L01.json.tmp"
    stale_txt_tmp.write_text("stale", encoding="utf-8")
    stale_json_tmp.write_text("stale", encoding="utf-8")

    def failing_transcriber(model, video_path, duration_s, clip_seconds, on_progress):
        # By the time transcriber is called, old JSON and stale .tmp must be gone
        assert not json_path.exists()
        assert not stale_txt_tmp.exists()
        assert not stale_json_tmp.exists()
        raise RuntimeError("simulated crash during transcription")

    args = argparse.Namespace(
        course_dir=str(course_dir),
        model="medium.en",
        compute_type="int8",
        threads=4,
        only="L01",
        clip_seconds=None,
        force=True,
        dry_run=False,
    )

    code = tl.run_pipeline(
        args,
        duration_reader=lambda p: 60.0,
        model_loader=lambda m, c, t: (None, "1.0"),
        transcriber=failing_transcriber,
    )
    assert code == 1
    # After crash, L01.json remains missing -> status is todo
    done, _ = tl.check_lecture_done(json_path)
    assert done is False


def test_clip_runs_isolation(tmp_path):
    """Clip run writes only to _trials/ and leaves transcripts folder untouched."""
    course_dir = tmp_path / "course"
    videos_dir = course_dir / "videos"
    videos_dir.mkdir(parents=True)
    (videos_dir / "L01.mp4").touch()

    transcripts_dir = course_dir / "derived" / "transcripts"
    transcripts_dir.mkdir(parents=True)

    # Existing L01 full files
    (transcripts_dir / "L01.txt").write_text("[00:00] original\n", encoding="utf-8")
    orig_payload = {
        "schema_version": 1,
        "clip_seconds": None,
        "segments": [{"id": 0, "text": "original"}],
    }
    (transcripts_dir / "L01.json").write_text(json.dumps(orig_payload), encoding="utf-8")

    def list_non_trial_files():
        return sorted([
            f.name
            for f in transcripts_dir.iterdir()
            if f.name not in ("_trials", "transcribe.log")
        ])

    snapshot_before = list_non_trial_files()

    def fake_transcriber(model, video_path, duration_s, clip_seconds, on_progress):
        assert clip_seconds == 15.0
        return (
            [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 5.0,
                    "text": "clip snippet",
                    "avg_logprob": -0.1,
                    "no_speech_prob": 0.0,
                    "compression_ratio": 1.0,
                }
            ],
            1.5,
            0.1,
        )

    args = argparse.Namespace(
        course_dir=str(course_dir),
        model="medium.en",
        compute_type="int8",
        threads=4,
        only="L01",
        clip_seconds=15.0,
        force=False,
        dry_run=False,
    )

    code = tl.run_pipeline(
        args,
        duration_reader=lambda p: 1000.0,
        model_loader=lambda m, c, t: (None, "1.0"),
        transcriber=fake_transcriber,
    )
    assert code == 0

    snapshot_after = list_non_trial_files()
    assert snapshot_after == snapshot_before

    trial_file = transcripts_dir / "_trials" / "L01-clip15-medium.en.json"
    assert trial_file.is_file()
    with open(trial_file, encoding="utf-8") as f:
        trial_data = json.load(f)
    assert trial_data["clip_seconds"] == 15.0
    assert trial_data["lecture"] == "L01"


def test_clip_with_force_never_touches_real_files(tmp_path):
    """Clip run with --force must never delete, write or touch L??.json or L??.txt."""
    course_dir = tmp_path / "course"
    videos_dir = course_dir / "videos"
    videos_dir.mkdir(parents=True)
    (videos_dir / "L01.mp4").touch()

    transcripts_dir = course_dir / "derived" / "transcripts"
    transcripts_dir.mkdir(parents=True)
    json_path = transcripts_dir / "L01.json"
    txt_path = transcripts_dir / "L01.txt"

    valid_payload = {
        "schema_version": 1,
        "lecture": "L01",
        "video_file": "videos/L01.mp4",
        "model": "medium.en",
        "compute_type": "int8",
        "threads": 4,
        "language": "en",
        "faster_whisper_version": "1.0",
        "settings": {
            "beam_size": 5,
            "vad_filter": True,
            "condition_on_previous_text": False,
            "initial_prompt": "",
        },
        "duration_s": 50.0,
        "clip_seconds": None,
        "transcribe_seconds": 5.0,
        "realtime_factor": 0.1,
        "created_at": "2026-10-07T00:00:00Z",
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 4.0,
                "text": "initial",
                "avg_logprob": -0.1,
                "no_speech_prob": 0.0,
                "compression_ratio": 1.0,
            }
        ],
    }
    json_path.write_text(json.dumps(valid_payload), encoding="utf-8")
    txt_path.write_text("[00:00] initial\n", encoding="utf-8")

    json_bytes_before = json_path.read_bytes()
    txt_bytes_before = txt_path.read_bytes()
    json_mtime_before = json_path.stat().st_mtime_ns
    txt_mtime_before = txt_path.stat().st_mtime_ns

    time.sleep(0.01)

    args = argparse.Namespace(
        course_dir=str(course_dir),
        model="medium.en",
        compute_type="int8",
        threads=4,
        only="L01",
        clip_seconds=10.0,
        force=True,  # with --force!
        dry_run=False,
    )

    code = tl.run_pipeline(
        args,
        duration_reader=lambda p: 50.0,
        model_loader=lambda m, c, t: (None, "1.0"),
        transcriber=lambda m, p, d, c, pr: (
            [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 2.0,
                    "text": "clip",
                    "avg_logprob": -0.1,
                    "no_speech_prob": 0.0,
                    "compression_ratio": 1.0,
                }
            ],
            1.0,
            0.1,
        ),
    )
    assert code == 0

    assert json_path.read_bytes() == json_bytes_before
    assert txt_path.read_bytes() == txt_bytes_before
    assert json_path.stat().st_mtime_ns == json_mtime_before
    assert txt_path.stat().st_mtime_ns == txt_mtime_before


def test_duration_reader_exception_handling(tmp_path, capsys):
    """Exception during duration reading is caught; reported in dry-run, clip, and full run."""
    course_dir = tmp_path / "course"
    videos_dir = course_dir / "videos"
    videos_dir.mkdir(parents=True)
    (videos_dir / "L01.mp4").touch()
    (videos_dir / "L02.mp4").touch()

    def failing_dur_reader(video_path):
        if video_path.stem.upper() == "L02":
            raise ValueError("Corrupt container header")
        return 50.0

    # 1. Dry run shows ? and reason for L02
    args_dry = argparse.Namespace(
        course_dir=str(course_dir),
        model="medium.en",
        compute_type="int8",
        threads=4,
        only=None,
        clip_seconds=None,
        force=False,
        dry_run=True,
    )
    code = tl.run_pipeline(
        args_dry,
        duration_reader=failing_dur_reader,
        model_loader=lambda m, c, t: (None, "1.0"),
    )
    assert code == 0
    captured = capsys.readouterr()
    assert "L01  00:50  todo" in captured.out
    assert "L02  ? (Corrupt container header)  todo" in captured.out

    # 2. Clip run leaves L02 out of estimate and notes it
    args_clip = argparse.Namespace(
        course_dir=str(course_dir),
        model="medium.en",
        compute_type="int8",
        threads=4,
        only=None,
        clip_seconds=10.0,
        force=False,
        dry_run=False,
    )
    code = tl.run_pipeline(
        args_clip,
        duration_reader=failing_dur_reader,
        model_loader=lambda m, c, t: (None, "1.0"),
        transcriber=lambda m, p, d, c, pr: (
            [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 2.0,
                    "text": "clip",
                    "avg_logprob": -0.1,
                    "no_speech_prob": 0.0,
                    "compression_ratio": 1.0,
                }
            ],
            1.0,
            0.1,
        ),
    )
    assert code == 0
    log_content = (course_dir / "derived" / "transcripts" / "transcribe.log").read_text(
        encoding="utf-8"
    )
    assert "excluding 1 lecture(s) with unknown duration" in log_content

    # 3. Full run attempts the lecture; if transcriber fails on bad file, counted as failed
    def failing_transcriber(m, p, d, c, pr):
        if p.stem.upper() == "L02":
            raise RuntimeError("Decode failed")
        return (
            [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 2.0,
                    "text": "ok",
                    "avg_logprob": -0.1,
                    "no_speech_prob": 0.0,
                    "compression_ratio": 1.0,
                }
            ],
            1.0,
            0.1,
        )

    args_full = argparse.Namespace(
        course_dir=str(course_dir),
        model="medium.en",
        compute_type="int8",
        threads=4,
        only=None,
        clip_seconds=None,
        force=True,
        dry_run=False,
    )
    code = tl.run_pipeline(
        args_full,
        duration_reader=failing_dur_reader,
        model_loader=lambda m, c, t: (None, "1.0"),
        transcriber=failing_transcriber,
    )
    assert code == 1
    assert (course_dir / "derived" / "transcripts" / "L01.json").exists()
    assert not (course_dir / "derived" / "transcripts" / "L02.json").exists()


def test_run_pipeline_error_resilience(tmp_path):
    """A failing lecture does not stop the next lecture; pipeline returns code 1."""
    course_dir = tmp_path / "course"
    videos_dir = course_dir / "videos"
    videos_dir.mkdir(parents=True)
    (videos_dir / "L01.mp4").touch()
    (videos_dir / "L02.mp4").touch()

    def fake_transcriber(model, video_path, duration_s, clip_seconds, on_progress):
        if video_path.stem.upper() == "L01":
            raise ValueError("Corrupted video bitstream")
        return (
            [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 2.0,
                    "text": "L02 text",
                    "avg_logprob": -0.1,
                    "no_speech_prob": 0.0,
                    "compression_ratio": 1.0,
                }
            ],
            1.0,
            0.1,
        )

    args = argparse.Namespace(
        course_dir=str(course_dir),
        model="medium.en",
        compute_type="int8",
        threads=4,
        only=None,
        clip_seconds=None,
        force=False,
        dry_run=False,
    )

    code = tl.run_pipeline(
        args,
        duration_reader=lambda p: 50.0,
        model_loader=lambda m, c, t: (None, "1.0"),
        transcriber=fake_transcriber,
    )
    assert code == 1

    transcripts_dir = course_dir / "derived" / "transcripts"
    assert not (transcripts_dir / "L01.json").exists()
    assert (transcripts_dir / "L02.json").exists()
    assert (transcripts_dir / "L02.txt").exists()


def test_cli_validation(tmp_path, capsys):
    """CLI exits 2 on missing course dir, missing videos folder, or unknown --only ID."""
    # 1. Missing course dir
    args = argparse.Namespace(
        course_dir=None,
        model="medium.en",
        compute_type="int8",
        threads=4,
        only=None,
        clip_seconds=None,
        force=False,
        dry_run=False,
    )
    old_env = sys.modules["os"].environ.pop("COURSE_DATA_DIR", None)
    try:
        assert tl.run_pipeline(args) == 2
    finally:
        if old_env is not None:
            sys.modules["os"].environ["COURSE_DATA_DIR"] = old_env

    # 2. No videos/ folder
    empty_course = tmp_path / "empty_course"
    empty_course.mkdir()
    args.course_dir = str(empty_course)
    assert tl.run_pipeline(args) == 2

    # 3. Unknown --only ID
    videos_dir = empty_course / "videos"
    videos_dir.mkdir()
    (videos_dir / "L01.mp4").touch()
    args.only = "L01,L99"
    ret = tl.run_pipeline(args)
    assert ret == 2
    captured = capsys.readouterr()
    assert "L99" in captured.err
    assert "L01" in captured.err


def test_dry_run(tmp_path, capsys):
    """--dry-run prints durations and status without loading the model."""
    course_dir = tmp_path / "course"
    videos_dir = course_dir / "videos"
    videos_dir.mkdir(parents=True)
    (videos_dir / "L01.mp4").touch()
    (videos_dir / "L02.mp4").touch()

    transcripts_dir = course_dir / "derived" / "transcripts"
    transcripts_dir.mkdir(parents=True)
    (transcripts_dir / "L01.json").write_text(
        json.dumps({
            "schema_version": 1,
            "clip_seconds": None,
            "segments": [{"id": 0, "text": "done"}],
            "model": "medium.en",
        }),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        course_dir=str(course_dir),
        model="medium.en",
        compute_type="int8",
        threads=4,
        only=None,
        clip_seconds=None,
        force=False,
        dry_run=True,
    )

    model_loaded = False

    def fake_loader(m, c, t):
        nonlocal model_loaded
        model_loaded = True
        return None, "1.0"

    code = tl.run_pipeline(
        args,
        duration_reader=lambda p: 3082.0,
        model_loader=fake_loader,
    )
    assert code == 0
    assert not model_loaded

    captured = capsys.readouterr()
    assert "L01  51:22  done (medium.en)" in captured.out
    assert "L02  51:22  todo" in captured.out
    assert "Total audio: 1:42:44 (2 lectures, 1 to do)" in captured.out


def test_progress_logging_every_5_minutes(tmp_path):
    """Progress lines are logged at 5-minute (300s) audio intervals."""
    course_dir = tmp_path / "course"
    videos_dir = course_dir / "videos"
    videos_dir.mkdir(parents=True)
    (videos_dir / "L01.mp4").touch()

    def fake_transcriber(model, video_path, duration_s, clip_seconds, on_progress):
        # Trigger progress at 310s (past 5m) and 620s (past 10m)
        on_progress(310.0)
        on_progress(620.0)
        return (
            [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 620.0,
                    "text": "all audio",
                    "avg_logprob": -0.1,
                    "no_speech_prob": 0.0,
                    "compression_ratio": 1.0,
                }
            ],
            5.0,
            0.1,
        )

    args = argparse.Namespace(
        course_dir=str(course_dir),
        model="medium.en",
        compute_type="int8",
        threads=4,
        only="L01",
        clip_seconds=None,
        force=False,
        dry_run=False,
    )

    code = tl.run_pipeline(
        args,
        duration_reader=lambda p: 900.0,
        model_loader=lambda m, c, t: (None, "1.0"),
        transcriber=fake_transcriber,
    )
    assert code == 0

    log_file = course_dir / "derived" / "transcripts" / "transcribe.log"
    log_content = log_file.read_text(encoding="utf-8")
    assert "L01 progress: 05:10 / 15:00" in log_content
    assert "L01 progress: 10:20 / 15:00" in log_content
    assert "Finished: 1 done, 0 failed, 0 skipped" in log_content


def test_keyboard_interrupt_and_trials_cleanup(tmp_path):
    """KeyboardInterrupt cleans up .tmp files in output and _trials, logs, and returns 130."""
    course_dir = tmp_path / "course"
    videos_dir = course_dir / "videos"
    videos_dir.mkdir(parents=True)
    (videos_dir / "L01.mp4").touch()

    transcripts_dir = course_dir / "derived" / "transcripts"
    trials_dir = transcripts_dir / "_trials"
    trials_dir.mkdir(parents=True)

    tmp_file = transcripts_dir / "L01.json.tmp"
    tmp_file.write_text("in progress", encoding="utf-8")
    trial_tmp_file = trials_dir / "L01-clip10-medium.en.json.tmp"
    trial_tmp_file.write_text("in progress trial", encoding="utf-8")

    def interrupting_transcriber(model, video_path, duration_s, clip_seconds, on_progress):
        raise KeyboardInterrupt()

    args = argparse.Namespace(
        course_dir=str(course_dir),
        model="medium.en",
        compute_type="int8",
        threads=4,
        only="L01",
        clip_seconds=10.0,
        force=False,
        dry_run=False,
    )

    code = tl.run_pipeline(
        args,
        duration_reader=lambda p: 50.0,
        model_loader=lambda m, c, t: (None, "1.0"),
        transcriber=interrupting_transcriber,
    )
    assert code == 130
    assert not tmp_file.exists()
    assert not trial_tmp_file.exists()
    log_file = transcripts_dir / "transcribe.log"
    assert "interrupted" in log_file.read_text(encoding="utf-8")
