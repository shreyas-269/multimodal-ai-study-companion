import argparse
import json
import os
import re
import sys
import time
import traceback
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

INITIAL_PROMPT = (
    "MIT 6.041 probability lecture by Professor John Tsitsiklis: sample space, axioms, "
    "conditional probability, Bayes' rule, independence, counting, permutations, "
    "combinations, random variables, PMF, expectation, variance, joint PMF."
)


class RunLogger:
    """Handles console and file logging with local timestamps."""

    def __init__(self, log_file: Path | None):
        self.log_file = log_file

    def log(self, message: str) -> None:
        local_time = datetime.now().strftime("%H:%M:%S")
        line = f"[{local_time}] {message}"
        print(line, flush=True)
        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
                f.flush()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    cpu_count = os.cpu_count() or 8
    default_threads = max(4, cpu_count // 2)

    parser = argparse.ArgumentParser(
        description="Transcribe course lecture videos using faster-whisper."
    )
    parser.add_argument(
        "--course-dir",
        type=str,
        default=None,
        help="Path to course folder (default: COURSE_DATA_DIR env var)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="medium.en",
        help="Whisper model name (default: medium.en)",
    )
    parser.add_argument(
        "--compute-type",
        type=str,
        default="int8",
        help="Compute type for inference (default: int8)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=default_threads,
        help=f"CPU threads to use (default: {default_threads})",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Comma-separated lecture IDs to transcribe (e.g. L01,L03)",
    )
    parser.add_argument(
        "--clip-seconds",
        type=float,
        default=None,
        help="Trial mode: transcribe only the first N seconds",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Redo finished lectures",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List lectures, durations and status without loading model",
    )
    return parser.parse_args(argv)


def format_time(seconds: float, force_hours: bool = False) -> str:
    secs = int(round(seconds))
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h > 0 or force_hours:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def read_video_duration(video_path: Path) -> float:
    """Read container duration using PyAV without decoding audio stream."""
    import av

    with av.open(str(video_path)) as container:
        if container.duration is not None:
            return float(container.duration / 1_000_000.0)
        max_duration = 0.0
        for stream in container.streams:
            if stream.duration is not None and stream.time_base is not None:
                dur = float(stream.duration * stream.time_base)
                if dur > max_duration:
                    max_duration = dur
        return max_duration


def discover_lectures(videos_dir: Path) -> dict[str, Path]:
    """Discover videos matching ^L\\d{2}\\.mp4 sorted by lecture ID."""
    pattern = re.compile(r"^L\d{2}$", re.IGNORECASE)
    lectures: dict[str, Path] = {}
    if not videos_dir.is_dir():
        return lectures
    for item in sorted(videos_dir.iterdir()):
        if item.is_file() and item.suffix.lower() == ".mp4" and pattern.match(item.stem):
            lid = item.stem.upper()
            lectures[lid] = item
    return dict(sorted(lectures.items()))


def check_lecture_done(json_path: Path) -> tuple[bool, str | None]:
    """Check if lecture is complete (valid schema 1, clip_seconds null, non-empty segments)."""
    if not json_path.is_file():
        return False, None
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False, None
        if data.get("schema_version") != 1:
            return False, None
        if data.get("clip_seconds") is not None:
            return False, None
        segments = data.get("segments")
        if not isinstance(segments, list) or len(segments) == 0:
            return False, None
        return True, data.get("model", "unknown")
    except Exception:
        return False, None


def clean_segments(raw_segments: Iterable[Any]) -> list[dict]:
    """Round fields, strip text, drop empty segments, and renumber IDs from 0."""
    cleaned: list[dict] = []
    idx = 0
    for seg in raw_segments:
        if isinstance(seg, dict):
            start = seg.get("start", 0.0)
            end = seg.get("end", 0.0)
            text = seg.get("text", "")
            avg_logprob = seg.get("avg_logprob", 0.0)
            no_speech_prob = seg.get("no_speech_prob", 0.0)
            compression_ratio = seg.get("compression_ratio", 0.0)
        else:
            start = getattr(seg, "start", 0.0)
            end = getattr(seg, "end", 0.0)
            text = getattr(seg, "text", "")
            avg_logprob = getattr(seg, "avg_logprob", 0.0)
            no_speech_prob = getattr(seg, "no_speech_prob", 0.0)
            compression_ratio = getattr(seg, "compression_ratio", 0.0)

        stripped = text.strip()
        if not stripped:
            continue

        cleaned.append({
            "id": idx,
            "start": round(float(start), 2),
            "end": round(float(end), 2),
            "text": stripped,
            "avg_logprob": round(float(avg_logprob), 3),
            "no_speech_prob": round(float(no_speech_prob), 3),
            "compression_ratio": round(float(compression_ratio), 3),
        })
        idx += 1
    return cleaned


def atomic_write_text(target_path: Path, content: str) -> None:
    """Atomically write text via temporary file in the same directory."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_name(f"{target_path.name}.tmp")
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target_path)


def atomic_write_json(target_path: Path, data: dict) -> None:
    """Atomically write JSON via temporary file in the same directory."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_name(f"{target_path.name}.tmp")
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target_path)


def cleanup_lecture_tmp(output_dir: Path, lecture_id: str) -> None:
    """Delete leftover .tmp files for a lecture in output_dir and _trials/."""
    for candidate in [
        output_dir / f"{lecture_id}.txt.tmp",
        output_dir / f"{lecture_id}.json.tmp",
    ]:
        if candidate.is_file():
            try:
                candidate.unlink()
            except Exception:
                pass

    trials_dir = output_dir / "_trials"
    if trials_dir.is_dir():
        for candidate in trials_dir.glob(f"{lecture_id}*.tmp"):
            if candidate.is_file():
                try:
                    candidate.unlink()
                except Exception:
                    pass


def load_whisper_model(model_name: str, compute_type: str, threads: int) -> tuple[Any, str]:
    """Lazily import faster-whisper and instantiate WhisperModel."""
    import faster_whisper

    version = getattr(faster_whisper, "__version__", "unknown")
    model = faster_whisper.WhisperModel(
        model_name,
        device="cpu",
        compute_type=compute_type,
        cpu_threads=threads,
    )
    return model, version


def transcribe_lecture(
    model: Any,
    video_path: Path,
    duration_s: float,
    clip_seconds: float | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> tuple[list[dict], float, float]:
    """Transcribe video or clip. Time starts right before transcribe and ends after generator."""
    if clip_seconds is not None:
        import faster_whisper

        audio = faster_whisper.decode_audio(str(video_path))
        num_samples = int(clip_seconds * 16000)
        audio = audio[:num_samples]
        actual_audio_dur = float(clip_seconds)
    else:
        audio = str(video_path)
        actual_audio_dur = duration_s

    t0 = time.perf_counter()
    segments_gen, _ = model.transcribe(
        audio,
        language="en",
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
        initial_prompt=INITIAL_PROMPT,
        word_timestamps=False,
    )
    raw_segments = []
    for seg in segments_gen:
        raw_segments.append(seg)
        if on_progress:
            on_progress(seg.end)
    transcribe_seconds = time.perf_counter() - t0
    rtf = transcribe_seconds / actual_audio_dur if actual_audio_dur > 0 else 0.0
    cleaned = clean_segments(raw_segments)
    return cleaned, transcribe_seconds, rtf


class ProgressTracker:
    """Tracks and logs 5-minute audio interval progress."""

    def __init__(self, logger: RunLogger, lecture_id: str, target_dur: float):
        self.logger = logger
        self.lecture_id = lecture_id
        self.target_dur = target_dur
        self.start_perf = time.perf_counter()
        self.next_mark = 300.0

    def on_progress(self, pos_s: float) -> None:
        if pos_s >= self.next_mark:
            elapsed = time.perf_counter() - self.start_perf
            pct = min(100.0, (pos_s / self.target_dur) * 100.0) if self.target_dur > 0 else 100.0
            rate = pos_s / elapsed if elapsed > 0 else 0.0
            remaining_audio = max(0.0, self.target_dur - pos_s)
            eta = remaining_audio / rate if rate > 0 else 0.0
            pos_fmt = format_time(pos_s)
            dur_fmt = format_time(self.target_dur)
            el_fmt = format_time(elapsed)
            eta_fmt = format_time(eta)
            self.logger.log(
                f"{self.lecture_id} progress: {pos_fmt} / {dur_fmt} ({pct:.1f}%), "
                f"elapsed {el_fmt}, ETA {eta_fmt}"
            )
            self.next_mark = (int(pos_s) // 300 + 1) * 300.0


def run_pipeline(
    args: argparse.Namespace,
    duration_reader: Callable[[Path], float] = read_video_duration,
    model_loader: Callable[[str, str, int], tuple[Any, str]] = load_whisper_model,
    transcriber: Callable[..., tuple[list[dict], float, float]] = transcribe_lecture,
    logger: RunLogger | None = None,
) -> int:
    course_dir_raw = args.course_dir or os.environ.get("COURSE_DATA_DIR")
    if not course_dir_raw:
        print(
            "Error: Course directory not specified and COURSE_DATA_DIR environment variable "
            "is not set.",
            file=sys.stderr,
        )
        return 2

    course_dir = Path(course_dir_raw)
    videos_dir = course_dir / "videos"
    if not videos_dir.is_dir():
        print(f"Error: Course folder '{course_dir}' has no videos/ subfolder.", file=sys.stderr)
        return 2

    all_lectures = discover_lectures(videos_dir)
    if not all_lectures:
        print(f"Error: No matching L??.mp4 lectures found in '{videos_dir}'.", file=sys.stderr)
        return 2

    valid_ids = list(all_lectures.keys())
    if args.only:
        req_ids = [item.strip().upper() for item in args.only.split(",") if item.strip()]
        unknown_ids = [item for item in req_ids if item not in all_lectures]
        if unknown_ids:
            un_str = ", ".join(unknown_ids)
            val_str = ", ".join(valid_ids)
            print(
                f"Error: Unknown lecture ID(s) in --only: {un_str}. Valid IDs: {val_str}",
                file=sys.stderr,
            )
            return 2
        selected_lectures = {lid: all_lectures[lid] for lid in req_ids}
    else:
        selected_lectures = all_lectures

    output_dir = course_dir / "derived" / "transcripts"
    trials_dir = output_dir / "_trials"

    if args.dry_run:
        print(f"Course folder: {course_dir}")
        print(f"Output folder: {output_dir}")
        total_audio = 0.0
        todo_count = 0
        for lid, vpath in sorted(selected_lectures.items()):
            try:
                dur = duration_reader(vpath)
                dur_str = format_time(dur)
                total_audio += dur
            except Exception as exc:
                dur = None
                dur_str = f"? ({exc})"
            is_done, done_model = check_lecture_done(output_dir / f"{lid}.json")
            status_str = f"done ({done_model})" if is_done else "todo"
            if not is_done:
                todo_count += 1
            print(f"{lid}  {dur_str}  {status_str}")
        total_str = format_time(total_audio, force_hours=True)
        print(f"Total audio: {total_str} ({len(selected_lectures)} lectures, {todo_count} to do)")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    if logger is None:
        logger = RunLogger(output_dir / "transcribe.log")

    # Pre-read durations and completion status with per-lecture error handling
    lecture_info: dict[str, tuple[Path, float | None, str | None, bool, str | None]] = {}
    for lid, vpath in selected_lectures.items():
        try:
            dur = duration_reader(vpath)
            dur_err = None
        except Exception as exc:
            dur = None
            dur_err = str(exc)
        is_done, done_model = check_lecture_done(output_dir / f"{lid}.json")
        lecture_info[lid] = (vpath, dur, dur_err, is_done, done_model)

    # Determine what needs execution
    needs_run = []
    skipped_count = 0
    for lid, (_vpath, _dur, _err, is_done, done_model) in lecture_info.items():
        if is_done and not args.force and args.clip_seconds is None:
            logger.log(f"{lid} skip (done with {done_model})")
            skipped_count += 1
        else:
            needs_run.append(lid)

    if not needs_run:
        logger.log(f"Finished: 0 done, 0 failed, {skipped_count} skipped")
        return 0

    # Load model once
    model, fw_version = model_loader(args.model, args.compute_type, args.threads)

    done_count = 0
    failed_count = 0
    current_lecture_id: str | None = None

    try:
        for lid in needs_run:
            current_lecture_id = lid
            vpath, dur, dur_err, _is_done, _done_model = lecture_info[lid]

            # Delete stale tmp files before processing
            cleanup_lecture_tmp(output_dir, lid)

            # Under --force in full-run path only, delete existing JSON before writing
            if args.force and args.clip_seconds is None:
                existing_json = output_dir / f"{lid}.json"
                if existing_json.is_file():
                    existing_json.unlink()

            if args.clip_seconds is not None:
                target_dur = args.clip_seconds
                dur_fmt = format_time(target_dur)
            else:
                target_dur = dur if dur is not None else 0.0
                dur_fmt = format_time(target_dur) if dur is not None else f"? ({dur_err})"

            logger.log(
                f"{lid} start: duration {dur_fmt}, model {args.model}, "
                f"compute_type {args.compute_type}, threads {args.threads}"
            )

            tracker = ProgressTracker(logger, lid, target_dur)

            try:
                # In full runs the lecture is still attempted even if duration pre-read failed
                segments, transcribe_secs, rtf = transcriber(
                    model,
                    vpath,
                    dur if dur is not None else 0.0,
                    args.clip_seconds,
                    tracker.on_progress,
                )

                created_at_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                actual_dur = dur if dur is not None else (segments[-1]["end"] if segments else 0.0)
                result_payload = {
                    "schema_version": 1,
                    "lecture": lid,
                    "video_file": f"videos/{vpath.name}",
                    "model": args.model,
                    "compute_type": args.compute_type,
                    "threads": args.threads,
                    "language": "en",
                    "faster_whisper_version": fw_version,
                    "settings": {
                        "beam_size": 5,
                        "vad_filter": True,
                        "condition_on_previous_text": False,
                        "initial_prompt": INITIAL_PROMPT,
                    },
                    "duration_s": round(actual_dur, 2),
                    "clip_seconds": round(args.clip_seconds, 2)
                    if args.clip_seconds is not None
                    else None,
                    "transcribe_seconds": round(transcribe_secs, 2),
                    "realtime_factor": round(rtf, 2),
                    "created_at": created_at_utc,
                    "segments": segments,
                }

                if args.clip_seconds is not None:
                    # Clip mode: write ONLY _trials/L??-clip{N}-{model}.json
                    trials_dir.mkdir(parents=True, exist_ok=True)
                    clip_tag = (
                        int(args.clip_seconds)
                        if args.clip_seconds == int(args.clip_seconds)
                        else args.clip_seconds
                    )
                    trial_json_path = trials_dir / f"{lid}-clip{clip_tag}-{args.model}.json"
                    atomic_write_json(trial_json_path, result_payload)

                    wall_fmt = format_time(transcribe_secs)
                    logger.log(
                        f"{lid} clip done: wall time {wall_fmt}, RTF {rtf:.2f}, "
                        f"{len(segments)} segments -> {trial_json_path.name}"
                    )

                    # Estimate full run, leaving out lectures with unknown duration
                    known_todo_durs = [
                        d for _lid, (_vp, d, _err, done, _m) in lecture_info.items()
                        if (not done or args.force) and d is not None
                    ]
                    unknown_todo_count = sum(
                        1 for _lid, (_vp, d, _err, done, _m) in lecture_info.items()
                        if (not done or args.force) and d is None
                    )
                    todo_lectures_dur = sum(known_todo_durs)
                    est_seconds = rtf * todo_lectures_dur
                    finish_time = datetime.now() + timedelta(seconds=est_seconds)
                    fin_fmt = finish_time.strftime("%H:%M:%S")
                    est_fmt = format_time(est_seconds, force_hours=True)
                    tot_fmt = format_time(todo_lectures_dur, force_hours=True)
                    unknown_note = (
                        f" (excluding {unknown_todo_count} lecture(s) with unknown duration)"
                        if unknown_todo_count > 0
                        else ""
                    )
                    logger.log(
                        f"Trial RTF: {rtf:.2f}. Full run estimate: {est_fmt}{unknown_note} "
                        f"(total todo audio {tot_fmt}), finish at {fin_fmt}"
                    )
                else:
                    # Full mode: write .txt first, then .json
                    txt_lines = []
                    for seg in segments:
                        txt_lines.append(f"[{format_time(seg['start'])}] {seg['text']}")
                    txt_content = "\n".join(txt_lines) + ("\n" if txt_lines else "")
                    atomic_write_text(output_dir / f"{lid}.txt", txt_content)
                    atomic_write_json(output_dir / f"{lid}.json", result_payload)

                    wall_fmt = format_time(transcribe_secs)
                    logger.log(
                        f"{lid} done: wall time {wall_fmt}, RTF {rtf:.2f}, "
                        f"{len(segments)} segments -> {lid}.json"
                    )

                done_count += 1
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                cleanup_lecture_tmp(output_dir, lid)
                logger.log(f"{lid} failed: {exc}")
                logger.log(traceback.format_exc().strip())
                failed_count += 1
                continue

    except KeyboardInterrupt:
        if current_lecture_id is not None:
            cleanup_lecture_tmp(output_dir, current_lecture_id)
        logger.log("interrupted")
        return 130

    logger.log(f"Finished: {done_count} done, {failed_count} failed, {skipped_count} skipped")
    return 1 if failed_count > 0 else 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args(argv)
    try:
        return run_pipeline(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
