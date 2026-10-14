"""`lungecuta import`: media records, one file each under `media/` (docs/design/02-media.md#ingest)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import music
from .decode import decoder_check
from .parallel import in_order
from .detect import RANGES, detect
from .files import content_hash, json_text, replace_if_unchanged, write_atomic, write_new
from .probe import probe
from .project import PROJECT_FILE, ROOTS_FILE
from .proxy import PARITY_BIAS, PARITY_P99, PROFILES, build_proxy, parity, parity_frames, passes, proxy_size
from .schema.media import (
    ColorDecided,
    ColorUndecided,
    FileStat,
    MediaPath,
    MediaRecord,
    Parity,
    Probe,
    ProxyRecord,
    VideoProbe,
)
from .schema.project import PROJECT_ROOT, ProjectFile, RootsFile
from .ticks import parse_rate

# Provisional: the only candidate that passed the parity gate on both samples
# (bench/RESULTS.md, "Proxy parity"); the choice is the user's (AGENTS.md, open decisions).
PROXY_PROFILE = "ffv1-444"
PROXY_DIR = Path("cache") / "proxy"


@dataclass(frozen=True)
class Project:
    directory: Path
    file: ProjectFile
    roots: dict[str, str]

    @classmethod
    def load(cls, directory: Path) -> Project:
        project = ProjectFile.model_validate_json((directory / PROJECT_FILE).read_text())
        roots = RootsFile.model_validate_json((directory / ROOTS_FILE).read_text()).root
        return cls(directory, project, roots)

    def root_path(self, name: str) -> Path:
        if name == PROJECT_ROOT:
            return self.directory
        assert name in self.file.media_roots, f"root {name!r} is not one of the project's media_roots"
        assert name in self.roots, f"root {name!r} is not placed on this machine; set it with lungecuta root"
        return Path(self.roots[name])


def set_root(directory: Path, name: str, place: Path) -> None:
    """List `name` among the project's media roots and place it at `place` on this machine."""
    assert place.is_dir(), f"{place} is not a directory"
    project_path = directory / PROJECT_FILE
    read_hash = content_hash(project_path)
    project = ProjectFile.model_validate_json(project_path.read_text())
    if name not in project.media_roots:
        project.media_roots.append(name)
        project = ProjectFile.model_validate(project.model_dump())
        replace_if_unchanged(project_path, read_hash, json_text(project.model_dump(mode="json")))
    roots_path = directory / ROOTS_FILE
    roots = RootsFile.model_validate_json(roots_path.read_text()).root
    roots[name] = str(place.resolve())
    write_atomic(roots_path, json_text(RootsFile.model_validate(roots).model_dump(mode="json")))


def load_record(project: Project, key: str) -> MediaRecord:
    path = project.directory / "media" / f"{key}.json"
    assert path.is_file(), f"no media record {key!r} in {project.directory / 'media'}"
    return MediaRecord.model_validate_json(path.read_text())


def original(project: Project, record: MediaRecord) -> Path:
    """The file a record describes, checked to be the one imported: by size and modification
    time, or else by its hash (docs/design/03-project-format.md#media)."""
    path = project.root_path(record.path.root) / record.path.rel
    assert path.is_file(), f"{record.key}: {path} is missing under root {record.path.root!r}"
    stat = path.stat()
    if (stat.st_size, stat.st_mtime_ns) != (record.stat.size, record.stat.mtime_ns):
        assert content_hash(path) == record.hash, (
            f"{record.key}: {path.name} has changed since it was imported, so its decisions are stale; "
            "import it again"
        )
    return path


def import_file(project: Project, file: Path, *, root: str, key: str | None = None, sound: bool = False) -> MediaRecord:
    file = file.resolve()
    assert file.is_file(), f"{file} is not a file"
    key = key or file.stem
    record_path = project.directory / "media" / f"{key}.json"
    assert not record_path.exists(), (
        f"media key {key!r} exists already ({record_path}); give this file another key with --key"
    )
    base = project.root_path(root).resolve()
    assert file.is_relative_to(base), f"{file} is not under root {root!r} ({base})"
    path = MediaPath(root=root, rel=file.relative_to(base).as_posix())

    stat = file.stat()
    probed = probe(file)
    video = probed.video
    common = dict(
        key=key, path=path, hash=content_hash(file), stat=FileStat(size=stat.st_size, mtime_ns=stat.st_mtime_ns),
        probe=probed, derived_from=None,
    )
    if video is None:
        record = _music(project, file, key, probed, common, sound)
    else:
        assert not sound, f"{file.name} has video; only a file of sound alone imports as a sound"
        detection = detect(file, probed)
        decode = decoder_check(file, pix_fmt=video.pix_fmt, size=video.size, frames=video.frames)
        proxy = _proxy(project, file, key, detection.color, video)
        record = MediaRecord(
            **common, time=detection.time, color=detection.color, decode=decode, proxy=proxy, wav=None, beats=None,
        )
    write_new(record_path, json_text(record.model_dump(mode="json")))
    return record


# Files imported at once by `import_files`: most of an import is one thread working on
# frames between decodes (bench/import_jobs.py, bench/RESULTS.md).
IMPORT_JOBS = 2


def import_files(project: Project, files: list[Path], *, root: str, key: str | None = None,
                 sound: bool = False) -> list[MediaRecord]:
    """`import_file` for each of `files`, IMPORT_JOBS at once, the records in the files'
    order. Keys are checked to differ before any work starts."""
    keys = [key or file.stem for file in files]
    assert key is None or len(files) == 1, "a key names one file"
    repeated = sorted({k for k in keys if keys.count(k) > 1})
    assert not repeated, f"files share the keys {repeated}; import them separately with --key"
    jobs = ((project, file) for file in files)
    return list(in_order(lambda project, file: import_file(project, file, root=root, key=key, sound=sound), jobs,
                         workers=IMPORT_JOBS, ahead=IMPORT_JOBS))


def _music(project: Project, file: Path, key: str, probed: Probe, common: dict[str, Any],
           sound: bool = False) -> MediaRecord:
    """A file without video is music, decoded once to the WAV and its beat map found in it;
    or, asked for, a sound (an effect, a voice) decoded the same way with no beat map."""
    assert len(probed.audio) == 1, f"{file.name} has {len(probed.audio)} audio streams; music has one"
    wav, signal = music.write_wav(project.directory, key, file, probed.audio[0])
    beats = None if sound else music.analyse(signal, wav.rate)
    return MediaRecord(**common, time=None, color=None, decode=None, proxy=None, wav=wav, beats=beats)


def _proxy(
    project: Project, file: Path, key: str, decision: ColorDecided | ColorUndecided, video: VideoProbe
) -> ProxyRecord | None:
    """Build the proxy in the original's own range and run its parity test; a proxy that
    fails the test is deleted and the import stops."""
    source_range = RANGES.get(video.tags.color_range)
    if source_range is None:
        return None  # without a stated range the proxy could not be decoded correctly
    chosen = PROFILES[PROXY_PROFILE]
    size = proxy_size(video.size, project.file.proxy_height)
    relative = PROXY_DIR / f"{key}{chosen.suffix}"
    target = project.directory / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    build_proxy(
        file, target, size=size, coded_size=video.size, source_pix_fmt=video.pix_fmt,
        source_chroma_location=video.chroma_location, source_range=source_range, profile=PROXY_PROFILE,
        proxy_range=source_range, frames=video.frames, rate=parse_rate(video.rate),
    )
    indices = parity_frames(video.frames)
    if isinstance(decision, ColorDecided):
        common = dict(
            indices=indices, rate=parse_rate(video.rate), coded_size=video.size, size=size,
            source_pix_fmt=video.pix_fmt, source_chroma_location=video.chroma_location,
            proxy_pix_fmt=chosen.decoded_pix_fmt, matrix=decision.matrix, source_range=source_range, proxy_range=source_range,
            config=project.file.color.config,
        )
        codes, di = parity(file, target, spaces=[None, decision.space], **common)
        if not passes(codes):
            target.unlink()
            raise AssertionError(
                f"proxy of {file.name} rejected: in R'G'B' codes p99 {codes.p99} (limit {PARITY_P99}), "
                f"bias {codes.bias} (limit {PARITY_BIAS} per channel)"
            )
        result = Parity(frames=indices, codes=codes, di=di, reason=None)
        matrix = decision.matrix
    else:
        result = Parity(
            frames=indices, codes=None, di=None,
            reason="the colour space is undecided, so the matrix to decode with is not known",
        )
        matrix = None
    return ProxyRecord(
        path=relative.as_posix(), profile=PROXY_PROFILE, pix_fmt=chosen.pix_fmt, size=size,
        matrix=matrix, range=source_range, parity=result,
    )


def summary(record: MediaRecord) -> dict[str, Any]:
    """What `lungecuta import` prints for one file; the record itself is in `media/KEY.json`."""
    if record.beats is not None:
        return {
            "key": record.key,
            "wav": record.wav.path,
            "beats": len(record.beats.times),
            "downbeats": len(record.beats.downbeats),
            "tempo": music.tempo(record.beats),
            "peak": record.wav.peak,
        }
    if record.wav is not None:
        return {"key": record.key, "wav": record.wav.path, "sound": True, "seconds": record.wav.samples / record.wav.rate,
                "peak": record.wav.peak}
    return {
        "key": record.key,
        "color": record.color.status,
        "space": record.color.space if isinstance(record.color, ColorDecided) else None,
        "time": record.time.status,
        "decoder": record.decode.decoder,
        "proxy_parity": record.proxy.parity.codes.model_dump() if record.proxy and record.proxy.parity.codes else None,
    }


def music_wav(project: Project, record: MediaRecord) -> Path:
    """The WAV a music record's beats were found in. The cache can be cleared (and is not
    committed), so a missing WAV is decoded again from the original and must come out
    byte for byte the same."""
    assert record.wav is not None, f"{record.key} is not music"
    path = project.directory / record.wav.path
    if not path.exists():
        rebuilt, _ = music.write_wav(project.directory, record.key, original(project, record), record.probe.audio[0])
        assert rebuilt.hash == record.wav.hash, (
            f"{record.key}: the WAV decoded again differs from the one its beats were found in "
            "(another ffmpeg or soxr?); import it again"
        )
    assert content_hash(path) == record.wav.hash, f"{record.key}: {path} has changed since import; import it again"
    return path


def relink(project: Project, name: str, place: Path) -> dict[str, Any]:
    """Point root `name` at `place`, where its files now are: each record's file is found
    under `place` by size and then by hash, never by name alone
    (docs/design/03-project-format.md#media). Every file of the root has to be found, and
    found once; a record whose file sits elsewhere under `place` than its old path gets its
    new path. Nothing is written unless all are found."""
    assert name != PROJECT_ROOT, f"the {PROJECT_ROOT!r} root is the project directory itself"
    assert name in project.file.media_roots, f"root {name!r} is not one of the project's media_roots"
    place = place.resolve()
    assert place.is_dir(), f"{place} is not a directory"
    records = [load_record(project, path.stem) for path in sorted((project.directory / "media").glob("*.json"))]
    records = [record for record in records if record.path.root == name]
    by_size: dict[int, list[Path]] = {}
    for path in place.rglob("*"):
        if path.is_file():
            by_size.setdefault(path.stat().st_size, []).append(path)
    found: dict[str, Path] = {}
    missing = []
    for record in records:
        same = [path for path in by_size.get(record.stat.size, []) if content_hash(path) == record.hash]
        assert len(same) <= 1, f"{record.key}: {len(same)} files under {place} have its content: {same}"
        if same:
            found[record.key] = same[0]
        else:
            missing.append(record.key)
    assert not missing, f"not found under {place} by size and hash: {missing}; nothing changed"
    moved = []
    for record in records:
        rel = found[record.key].relative_to(place).as_posix()
        if rel != record.path.rel:
            record_path = project.directory / "media" / f"{record.key}.json"
            read_hash = content_hash(record_path)
            stat = found[record.key].stat()
            updated = record.model_copy(update={
                "path": MediaPath(root=name, rel=rel), "stat": FileStat(size=stat.st_size, mtime_ns=stat.st_mtime_ns),
            })
            updated = MediaRecord.model_validate(updated.model_dump())
            replace_if_unchanged(record_path, read_hash, json_text(updated.model_dump(mode="json")))
            moved.append({"media": record.key, "from": record.path.rel, "to": rel})
    roots_path = project.directory / ROOTS_FILE
    roots = RootsFile.model_validate_json(roots_path.read_text()).root
    roots[name] = str(place)
    write_atomic(roots_path, json_text(RootsFile.model_validate(roots).model_dump(mode="json")))
    return {"root": name, "place": str(place), "records": len(records), "paths_changed": moved}
