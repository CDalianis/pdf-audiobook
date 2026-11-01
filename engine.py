"""Turn a text PDF into an English or Greek audiobook."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
CACHE_DIR = APP_DIR / "cache"

LANGUAGES = {
    "en": {
        "label": "English",
        "folder": "English",
        "voices": [
            ("en-GB-SoniaNeural", "Sonia · British"),
            ("en-GB-RyanNeural", "Ryan · British"),
            ("en-US-JennyNeural", "Jenny · American"),
            ("en-US-GuyNeural", "Guy · American"),
        ],
    },
    "el": {
        "label": "Greek",
        "folder": "Greek",
        "voices": [
            ("el-GR-AthinaNeural", "Athina"),
            ("el-GR-NestorasNeural", "Nestoras"),
        ],
    },
}

SPEECH_RATE = "-8%"
SPEECH_CHUNK = 2800
TRANSLATE_CHUNK = 1200
PART_WORDS = 1600

HEADING = re.compile(
    r"^(?:chapter|letter|part|book|prologue|epilogue|preface|introduction|"
    r"κεφάλαιο|επιστολή|επιστολη|μέρος|μερος|πρόλογος|προλογος|εισαγωγή|εισαγωγη)\b.{0,80}$",
    re.IGNORECASE,
)
ABBREVIATION = re.compile(
    r"(?:Mr|Mrs|Ms|Dr|St|Sr|Jr|Prof|Rev|Gen|Col|Capt|No|vs|etc|Dec|Jan|Feb|Aug|Sept|Oct|Nov)\.$",
    re.IGNORECASE,
)


class Cancelled(Exception):
    pass


@dataclass
class Section:
    title: str
    text: str


def default_voice(language: str) -> str:
    return LANGUAGES[language]["voices"][0][0]


def voice_label(language: str, voice: str) -> str:
    for voice_id, label in LANGUAGES[language]["voices"]:
        if voice_id == voice:
            return label
    return voice


def safe_name(value: str, limit: int = 70) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned or "audiobook")[:limit]


def output_dir_for(root: Path, pdf_path: Path, source: str, target: str, voice: str, export: str = "parts") -> Path:
    voice_name = voice_label(target, voice).split("·")[0].strip()
    if source == target:
        language = LANGUAGES[target]["folder"]
    else:
        language = f"{LANGUAGES[source]['folder']} to {LANGUAGES[target]['folder']}"
    kind = "Chapters" if export == "parts" else "Single file"
    return root / safe_name(pdf_path.stem) / f"{language} - {voice_name} - {kind}"


def clean_page(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    paragraphs: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        flat = re.sub(r"\s+", " ", " ".join(buffer)).strip()
        buffer.clear()
        if flat and not re.fullmatch(r"\d{1,4}", flat):
            paragraphs.append(flat)

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if re.fullmatch(r"\d{1,4}", stripped):
            continue
        buffer.append(stripped)
    flush()
    return "\n\n".join(paragraphs)


def extract_pages(pdf_path: Path) -> list[str]:
    import pymupdf

    try:
        document = pymupdf.open(pdf_path)
    except Exception as error:
        raise RuntimeError(f"Could not open this PDF. {error}") from error
    if document.needs_pass:
        document.close()
        raise RuntimeError("This PDF is password protected.")
    pages = [clean_page(page.get_text("text") or "") for page in document]
    document.close()
    return pages


def is_heading(paragraph: str) -> bool:
    line = paragraph.strip()
    if not line or len(line) > 80 or len(line.split()) > 10:
        return False
    return bool(HEADING.match(line))


def page_label(start: int, end: int, source: str) -> str:
    if source == "el":
        if start == end:
            return f"Σελίδα {start}."
        return f"Σελίδες {start} έως {end}."
    if start == end:
        return f"Page {start}."
    return f"Pages {start} to {end}."


def sections_from_pages(pages: list[str], source: str) -> list[Section]:
    usable = [(number, text) for number, text in enumerate(pages, start=1) if len(text.split()) >= 8]
    if not usable:
        raise RuntimeError(
            "This PDF has no readable text. Scanned pages need a text layer before they can be narrated."
        )

    paragraphs: list[tuple[int, str]] = []
    for number, text in usable:
        for paragraph in text.split("\n\n"):
            paragraph = paragraph.strip()
            if paragraph:
                paragraphs.append((number, paragraph))

    headings = [paragraph for _, paragraph in paragraphs if is_heading(paragraph)]
    if len(headings) >= 2:
        sections: list[Section] = []
        bucket: list[str] = []
        start_page = paragraphs[0][0]
        last_page = start_page
        for number, paragraph in paragraphs:
            if is_heading(paragraph) and bucket:
                sections.append(Section(bucket[0] if is_heading(bucket[0]) else page_label(start_page, last_page, source), "\n\n".join(bucket)))
                bucket = [paragraph]
                start_page = number
            else:
                if not bucket:
                    start_page = number
                bucket.append(paragraph)
            last_page = number
        if bucket:
            heading = bucket[0] if is_heading(bucket[0]) else page_label(start_page, last_page, source)
            sections.append(Section(heading, "\n\n".join(bucket)))
        return sections

    sections = []
    bucket = []
    words = 0
    start_page = usable[0][0]
    last_page = start_page
    for number, text in usable:
        if not bucket:
            start_page = number
        bucket.append(text)
        words += len(text.split())
        last_page = number
        if words >= PART_WORDS:
            label = page_label(start_page, last_page, source)
            sections.append(Section(label, "\n\n".join([label, *bucket])))
            bucket = []
            words = 0
    if bucket:
        label = page_label(start_page, last_page, source)
        sections.append(Section(label, "\n\n".join([label, *bucket])))
    return sections


def split_sentences(paragraph: str) -> list[str]:
    sentences: list[str] = []
    start = 0
    for match in re.finditer(r"[.!?]\s+", paragraph):
        if ABBREVIATION.search(paragraph[start : match.start() + 1].rstrip()):
            continue
        sentences.append(paragraph[start : match.end()].strip())
        start = match.end()
    tail = paragraph[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences or [paragraph.strip()]


def split_chunks(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    buffer = ""
    for paragraph in [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]:
        pieces = split_sentences(paragraph) if len(paragraph) > limit else [paragraph]
        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            while len(piece) > limit:
                cut = piece[:limit].rsplit(" ", 1)[0] or piece[:limit]
                chunks.append(cut)
                piece = piece[len(cut) :].strip()
            candidate = f"{buffer} {piece}".strip() if buffer else piece
            if buffer and len(candidate) > limit:
                chunks.append(buffer)
                buffer = piece
            else:
                buffer = candidate
    if buffer:
        chunks.append(buffer)
    return [chunk for chunk in chunks if chunk.strip()]


def greek_letters(text: str) -> int:
    return sum(1 for char in text if "\u0370" <= char <= "\u03ff" or "\u1f00" <= char <= "\u1fff")


def latin_letters(text: str) -> int:
    return sum(1 for char in text if "a" <= char.lower() <= "z")


def acceptable(text: str, language: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    alphabetic = sum(1 for char in stripped if char.isalpha())
    if alphabetic < 20:
        return True
    if language == "el":
        return greek_letters(stripped) >= max(8, alphabetic * 0.25)
    return latin_letters(stripped) >= max(8, alphabetic * 0.4)


def translate(text: str, source: str, target: str, cache_path: Path, on_status, should_stop) -> str:
    if source == target:
        return text
    if cache_path.exists():
        cached = cache_path.read_text(encoding="utf-8")
        if acceptable(cached, target):
            return cached
    import translators

    target_name = LANGUAGES[target]["label"]
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    total = sum(len(split_chunks(part, TRANSLATE_CHUNK)) for part in paragraphs)
    translated: list[str] = []
    done = 0
    for paragraph in paragraphs:
        pieces: list[str] = []
        for chunk in split_chunks(paragraph, TRANSLATE_CHUNK):
            if should_stop():
                raise Cancelled()
            done += 1
            on_status(f"Translating into {target_name}... {done} of {total}")
            pieces.append(_translate_chunk(translators, chunk, source, target))
            time.sleep(0.25)
        translated.append(" ".join(pieces))
    result = "\n\n".join(translated)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(result, encoding="utf-8")
    return result


def _translate_chunk(translators, text: str, source: str, target: str) -> str:
    last_error: Exception | None = None
    for engine in ("bing", "google"):
        for attempt in range(3):
            try:
                result = translators.translate_text(
                    text,
                    translator=engine,
                    from_language=source,
                    to_language=target,
                )
                if result and acceptable(result, target):
                    return result.strip()
                last_error = RuntimeError(f"{engine} did not return {LANGUAGES[target]['label']} text")
            except Exception as error:
                last_error = error
                time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"Could not translate a passage. {last_error}")


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def tag_mp3(path: Path, title: str, album: str, language_label: str) -> None:
    tagged = path.with_suffix(".tagged.mp3")
    subprocess.run(
        [
            ffmpeg_exe(),
            "-y",
            "-i",
            str(path),
            "-c",
            "copy",
            "-metadata",
            f"title={title}",
            "-metadata",
            f"album={album}",
            "-metadata",
            f"comment={language_label} narration",
            str(tagged),
        ],
        check=True,
        capture_output=True,
    )
    tagged.replace(path)


def concat_mp3(parts: list[Path], dest: Path) -> None:
    listing = dest.with_suffix(".concat.txt")
    lines = []
    for part in parts:
        escaped = part.resolve().as_posix().replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(
        [
            ffmpeg_exe(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )
    listing.unlink(missing_ok=True)


def write_playlist(folder: Path, files: list[Path]) -> None:
    lines = ["#EXTM3U", *[file.name for file in files]]
    (folder / "playlist.m3u").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


async def _speak_one(text: str, voice: str, dest: Path) -> None:
    import edge_tts

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(text, voice, rate=SPEECH_RATE)
            await communicate.save(str(dest))
            if dest.stat().st_size < 400:
                raise RuntimeError("The voice returned empty audio.")
            return
        except Exception as error:
            last_error = error
            await asyncio.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"Speech failed. {last_error}")


async def _speak_chunks(chunks: list[str], voice: str, dest: Path, should_stop) -> None:
    part_paths = [dest.with_name(f".{dest.stem}.{index:03d}.mp3") for index in range(len(chunks))]
    semaphore = asyncio.Semaphore(4)

    async def one(text: str, part: Path) -> None:
        if should_stop():
            raise Cancelled()
        async with semaphore:
            if should_stop():
                raise Cancelled()
            await _speak_one(text, voice, part)

    try:
        await asyncio.gather(*(one(text, part) for text, part in zip(chunks, part_paths)))
        if len(part_paths) == 1:
            part_paths[0].replace(dest)
            part_paths.clear()
        else:
            concat_mp3(part_paths, dest)
    finally:
        for part in part_paths:
            part.unlink(missing_ok=True)


def speak(text: str, voice: str, dest: Path, should_stop) -> None:
    asyncio.run(_speak_chunks(split_chunks(text, SPEECH_CHUNK), voice, dest, should_stop))


def build_audiobook(
    pdf_path: Path,
    source_language: str,
    output_language: str,
    voice: str,
    output_root: Path,
    scope: str,
    on_progress,
    on_status,
    should_stop,
    export: str = "parts",
) -> Path:
    pdf_path = Path(pdf_path)
    if source_language not in LANGUAGES or output_language not in LANGUAGES:
        raise RuntimeError("Choose English or Greek for the text and for the narration.")
    if pdf_path.suffix.lower() != ".pdf" or not pdf_path.is_file():
        raise RuntimeError("Choose a PDF file.")
    if should_stop():
        raise Cancelled()

    on_status("Reading the PDF...")
    on_progress(0.02, "Reading the PDF...")
    pages = extract_pages(pdf_path)
    sections = sections_from_pages(pages, source_language)
    if export not in ("parts", "single"):
        raise RuntimeError("Choose chapters or a single file.")
    if scope == "first":
        sections = sections[:1]

    folder = output_dir_for(output_root, pdf_path, source_language, output_language, voice, export)
    folder.mkdir(parents=True, exist_ok=True)
    album = safe_name(pdf_path.stem, 80)
    language_label = LANGUAGES[output_language]["label"]
    parts_dir = folder / ".parts" if export == "single" else folder
    if export == "single":
        parts_dir.mkdir(parents=True, exist_ok=True)
    chapter_files: list[Path] = []
    total = len(sections)

    for index, section in enumerate(sections):
        if should_stop():
            raise Cancelled()
        title = safe_name(section.title.rstrip("."), 60)
        dest = parts_dir / f"{index + 1:02d} - {title}.mp3"
        chapter_files.append(dest)
        base = index / total
        if dest.exists() and dest.stat().st_size > 1000:
            on_progress((index + 1) / total, f"Already saved · {title}")
            on_status(f"Already saved · {title}")
            continue

        prose = section.text.strip()
        if source_language != output_language:
            digest = hashlib.sha1(prose.encode("utf-8")).hexdigest()[:10]
            cache = CACHE_DIR / safe_name(pdf_path.stem) / f"{source_language}-{output_language}" / f"{index + 1:03d}-{digest}.txt"
            on_progress(base + 0.05 / total, f"Translating {title}...")

            def chunk_status(message, _title=title, _base=base, _total=total):
                on_status(f"{_title} · {message}")
                on_progress(_base + 0.35 / _total, f"{_title} · {message}")

            prose = translate(prose, source_language, output_language, cache, chunk_status, should_stop)

        on_progress(base + 0.5 / total, f"Narrating {title}...")
        on_status(f"Narrating {title}...")
        speak(prose, voice, dest, should_stop)
        tag_mp3(dest, title, album, language_label)
        on_progress((index + 1) / total, f"Saved {title}")
        on_status(f"Saved {dest.name}")

    if export == "single":
        joined = folder / f"{album}.mp3"
        on_status("Joining the chapters into one file...")
        concat_mp3(chapter_files, joined)
        tag_mp3(joined, album, album, language_label)
        shutil.rmtree(parts_dir, ignore_errors=True)
    else:
        write_playlist(folder, chapter_files)
    on_progress(1.0, "Audiobook ready")
    on_status(f"Audiobook ready · {folder}")
    return folder


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Make an English or Greek audiobook from a PDF.")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--from-lang", choices=sorted(LANGUAGES), default="en")
    parser.add_argument("--to-lang", choices=sorted(LANGUAGES), default="en")
    parser.add_argument("--voice", default=None)
    parser.add_argument("--scope", choices=("full", "first"), default="full")
    parser.add_argument("--export", choices=("parts", "single"), default="parts")
    parser.add_argument("--output", type=Path, default=APP_DIR / "audiobooks")
    args = parser.parse_args()
    voice = args.voice or default_voice(args.to_lang)

    def on_progress(fraction: float, message: str) -> None:
        print(f"{fraction * 100:5.1f}%  {message}", flush=True)

    folder = build_audiobook(
        args.pdf,
        args.from_lang,
        args.to_lang,
        voice,
        args.output,
        args.scope,
        on_progress,
        on_progress,
        lambda: False,
        args.export,
    )
    print(folder)


if __name__ == "__main__":
    _cli()
