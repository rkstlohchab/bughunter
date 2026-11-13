"""Bounded format detection, Office text extraction, and public resource downloads.

No document macros, scripts, formulas, external relationships, or HTML scripts run.
"""
from __future__ import annotations

import codecs
import http.client
import ipaddress
import posixpath
import re
import socket
import ssl
import stat
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

from .policy import canonical_url

MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000
MAX_ARCHIVE_DEPTH = 3
MAX_TEXT_BYTES = 20 * 1024 * 1024
MAX_WEB_BYTES = 64 * 1024 * 1024
MEDIA_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mpg", ".mpeg", ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp", ".gif"}


def archive_members(archive):
    items = archive.infolist()
    if len(items) > MAX_ARCHIVE_ENTRIES:
        raise ValueError("Archive contains more than 1,000 entries; split it into smaller archives.")
    names = set()
    for item in items:
        path = PurePosixPath(item.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in item.filename or re.match(r"^[A-Za-z]:", item.filename) or any(ord(c)<32 for c in item.filename):
            raise ValueError(f"Archive contains an unsafe member path: {item.filename!r}")
        if stat.S_ISLNK(item.external_attr >> 16):
            raise ValueError("Archive contains symbolic links; import a regular-file archive.")
        if item.filename in names:
            raise ValueError("Archive contains duplicate member names.")
        names.add(item.filename)
        if item.file_size > MAX_ARCHIVE_BYTES or (item.file_size > 1024 * 1024 and item.file_size > max(item.compress_size,1)*200):
            raise ValueError("Archive expansion exceeds the file size or compression-ratio limit.")
    if sum(i.file_size for i in items) > MAX_ARCHIVE_BYTES:
        raise ValueError("Archive expanded content exceeds 256 MiB; split it before importing.")
    return items


def decode_text(raw: bytes) -> str:
    if raw.startswith((codecs.BOM_UTF32_LE,codecs.BOM_UTF32_BE)):
        return raw.decode("utf-32")
    if raw.startswith((codecs.BOM_UTF16_LE,codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16")
    if b"\x00" in raw:
        raise ValueError("Binary data does not have a supported text parser.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    if text and sum(not c.isprintable() and c not in "\r\n\t\f" for c in text)/len(text) > .02:
        raise ValueError("Binary data does not have a supported text parser.")
    return text


def detect_format(path: Path, filename: str | None = None) -> str:
    suffix = Path(filename or path.name).suffix.lower()
    with path.open("rb") as stream: head = stream.read(8192)
    if head.startswith(b"%PDF-"): return ".pdf"
    if head.startswith((b"PK\x03\x04",b"PK\x05\x06",b"PK\x07\x08")):
        with zipfile.ZipFile(path) as archive:
            names = {i.filename for i in archive_members(archive)}
            if "word/document.xml" in names: return ".docx"
            if "xl/workbook.xml" in names: return ".xlsx"
            if "ppt/presentation.xml" in names: return ".pptx"
            if "META-INF/container.xml" in names: return ".epub"
            if "content.xml" in names and "mimetype" in names: return ".odt"
            if suffix == ".epub": return ".epub"
        return ".zip"
    if head.startswith(b"\xd0\xcf\x11\xe0"): return "legacy-office"
    if head.startswith((b"\x7fELF",b"MZ",b"\xcf\xfa\xed\xfe",b"\xce\xfa\xed\xfe",b"\xfe\xed\xfa")): return "executable"
    if head[:4] in {b'\xca\xfe\xba\xbe',b'\xbe\xba\xfe\xca',b'\xca\xfe\xba\xbf',b'\xbf\xba\xfe\xca'} and len(head)>=8:
        endian='big' if head[:4] in {b'\xca\xfe\xba\xbe',b'\xca\xfe\xba\xbf'} else 'little'
        if 1<=int.from_bytes(head[4:8],endian)<=32:return 'executable'
    if head.startswith((b"\x89PNG\r\n",b"\xff\xd8\xff",b"GIF87a",b"GIF89a",b"II*\x00",b"MM\x00*",b"BM")) or head[:4] == b"RIFF" and head[8:12] == b"WEBP": return ".image"
    if suffix in IMAGE_EXTENSIONS: return ".image"
    if suffix in MEDIA_EXTENSIONS: return suffix
    if head[4:8] == b"ftyp" or head.startswith(b"\x1aE\xdf\xa3"): return ".mp4"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE": return ".wav"
    if head.startswith((b"ID3",b"OggS",b"fLaC")): return ".mp3"
    if suffix in {".pdf", ".epub", ".docx", ".pptx", ".xlsx", ".zip"}: return suffix
    if suffix in {".srt", ".vtt", ".html", ".htm"}: return suffix
    if re.search(br"<(?:!doctype\s+html|html|head|body|article|main|h1)(?:\s|>)",head,re.I): return ".html"
    if suffix in {".doc", ".xls", ".ppt", ".pages", ".numbers", ".key", ".rar", ".7z", ".gz", ".rtf"}: return "unsupported"
    try:
        # A format probe may end halfway through a multibyte character.
        try: codecs.getincrementaldecoder("utf-8-sig")().decode(head,final=False)
        except UnicodeError: decode_text(head)
        else:
            if b"\x00" in head: decode_text(head)
            if head and sum(c<32 and c not in (9,10,12,13) for c in head)/len(head) > .02: decode_text(head)
    except (ValueError,UnicodeError): return "unsupported"
    return ".text"


def read_text(path):
    if path.stat().st_size > MAX_TEXT_BYTES: raise ValueError("Text resources exceed the 20 MiB parsing limit; split this file.")
    return decode_text(path.read_bytes())


def _xml(archive, name):
    item = archive.getinfo(name)
    if item.file_size > MAX_TEXT_BYTES: raise ValueError("An Office XML part exceeds 20 MiB.")
    raw = archive.read(item)
    # XML declarations encoded as UTF-16 must receive the same checks.
    text = decode_text(raw)
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b",text,re.I): raise ValueError("Document XML entity declarations are unsupported.")
    return ET.fromstring(text)


def _tag(element): return element.tag.rsplit("}",1)[-1]
def _attr(element, name): return next((v for k,v in element.attrib.items() if k.endswith("}"+name)),element.get(name,""))
def _content(element): return "".join("\t" if _tag(e) == "tab" else "\n" if _tag(e) == "br" else e.text or "" for e in element.iter() if _tag(e) in {"t","tab","br"})


def _relationships(archive, part):
    rel = posixpath.join(posixpath.dirname(part),"_rels",posixpath.basename(part)+".rels")
    if rel not in archive.namelist(): return {}
    return {e.attrib["Id"]:posixpath.normpath(posixpath.join(posixpath.dirname(part),e.attrib["Target"])).lstrip("/")
            for e in _xml(archive,rel) if e.attrib.get("TargetMode") != "External" and "Id" in e.attrib and "Target" in e.attrib}


def extract_office(path, kind):
    segments, warnings = [], []
    with zipfile.ZipFile(path) as archive:
        archive_members(archive)
        names = set(archive.namelist())
        if any("vbaProject" in name for name in names): warnings.append("Macros were ignored; only document text was parsed.")
        if kind == ".docx":
            for part in ["word/document.xml"] + sorted(n for n in names if re.fullmatch(r"word/(?:header\d+|footer\d+|footnotes|endnotes)\.xml",n)):
                for index,p in enumerate((e for e in _xml(archive,part).iter() if _tag(e) == "p"),1):
                    if text := _content(p).strip():
                        segments.append({"text":text,"locator":f"Paragraph {index}" if part == "word/document.xml" else f"{part} · Paragraph {index}","modality":"text"})
        elif kind == ".pptx":
            part = "ppt/presentation.xml"
            rels = _relationships(archive,part)
            slides = [rels.get(_attr(e,"id")) for e in _xml(archive,part).iter() if _tag(e) == "sldId"]
            for index,slide in enumerate(slides,1):
                if slide not in names:
                    warnings.append(f"Slide {index} has a missing/unsupported relationship."); continue
                text = "\n".join(_content(p) for p in _xml(archive,slide).iter() if _tag(p) == "p")
                segments.append({"text":text,"locator":f"Slide {index}","modality":"text","slide":index})
                notes = [target for target in _relationships(archive,slide).values() if target.startswith("ppt/notesSlides/") and target in names]
                for note in notes:
                    segments.append({"text":"\n".join(_content(p) for p in _xml(archive,note).iter() if _tag(p) == "p"),"locator":f"Slide {index} · Notes","modality":"text","slide":index})
            warnings.append("Slide text and notes were parsed; embedded diagrams and media need visual review.")
        elif kind == ".xlsx":
            shared = ["".join(t.text or "" for t in si.iter() if _tag(t) == "t") for si in _xml(archive,"xl/sharedStrings.xml")] if "xl/sharedStrings.xml" in names else []
            rels = _relationships(archive,"xl/workbook.xml")
            for sheet in (s for s in _xml(archive,"xl/workbook.xml").iter() if _tag(s) == "sheet"):
                part = rels.get(_attr(sheet,"id"))
                if part not in names:
                    warnings.append(f"Sheet {sheet.get('name','?')} has a missing/unsupported relationship."); continue
                for row in (r for r in _xml(archive,part).iter() if _tag(r) == "row"):
                    cells = []
                    for cell in row:
                        if _tag(cell) != "c": continue
                        value = next((c.text or "" for c in cell if _tag(c) == "v"),"")
                        if cell.get("t") == "s": value = shared[int(value)] if value.isdigit() and int(value)<len(shared) else "[invalid string reference]"
                        if cell.get("t") == "inlineStr": value = _content(cell)
                        formula = next((c.text for c in cell if _tag(c) == "f"),None)
                        if formula: value += f" [formula text: {formula}; cached value only]"
                        cells.append(f"{cell.get('r','cell')}: {value}")
                    segments.append({"text":" | ".join(cells),"locator":f"Sheet {sheet.get('name','?')} · Row {row.get('r','?')}","modality":"text"})
            warnings.append("Spreadsheet formulas were not evaluated; values are stored text or cached results.")
        else:
            for index,p in enumerate((e for e in _xml(archive,"content.xml").iter() if _tag(e) in {"p","h"}),1):
                segments.append({"text":"".join(p.itertext()),"locator":f"Paragraph {index}","modality":"text"})
            warnings.append("OpenDocument text was extracted; visual layout and embedded media were not interpreted.")
    return segments,warnings


def public_destination(url):
    url = canonical_url(url)
    parsed = urlsplit(url)
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(parsed.hostname,parsed.port or (443 if parsed.scheme == "https" else 80),type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError("Website imports require a public destination; private, loopback, and reserved addresses are unavailable.")
    return url, addresses


class _PinnedHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.create_connection((self.pinned_ip,self.port),timeout=self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        self.sock = socket.create_connection((self.pinned_ip,self.port),timeout=self.timeout)
        self.sock = self._context.wrap_socket(self.sock,server_hostname=self.host)


def _connect(url, addresses):
    parsed = urlsplit(url)
    if parsed.scheme == 'https':
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    for index, address in enumerate(addresses):
        connection = (_PinnedHTTPS(parsed.hostname,parsed.port,timeout=10,context=context) if parsed.scheme == 'https'
                      else _PinnedHTTP(parsed.hostname,parsed.port,timeout=10))
        connection.pinned_ip = address
        try:
            connection.connect()
            return connection
        except OSError:
            connection.close()
            if index == len(addresses)-1: raise


def fetch_resource(url, destination, cancelled=lambda: False):
    """Fetch a single explicitly selected resource; validate and pin every hop."""
    deadline = time.monotonic()+60
    temporary = destination.with_suffix(".part")
    try:
        for _ in range(6):
            if cancelled(): raise ValueError("Resource import cancelled.")
            url,addresses = public_destination(url)
            parsed = urlsplit(url)
            connection = _connect(url,addresses)
            try:
                connection.request("GET",(parsed.path or "/")+("?"+parsed.query if parsed.query else ""),headers={"User-Agent":"BugHunterClaudex/0.1 KnowledgeImporter","Accept-Encoding":"identity","Accept":"*/*","Connection":"close"})
                response = connection.getresponse()
                if response.status in {301,302,303,307,308}:
                    location = response.getheader("Location")
                    if not location: raise ValueError("Website redirect has no destination.")
                    url = urljoin(url,location)
                    continue
                if response.status != 200: raise ValueError(f"Website returned HTTP {response.status}. Import an accessible page or a local export.")
                if response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
                    raise ValueError("Website ignored the uncompressed-download request; save a local copy to import.")
                length = response.getheader("Content-Length")
                if length and int(length)>MAX_WEB_BYTES: raise ValueError("Website download exceeds 64 MiB; use a local file path for larger resources.")
                size = 0
                with temporary.open("wb") as output:
                    while True:
                        if time.monotonic()>deadline or cancelled(): raise ValueError("Resource import timed out or was cancelled.")
                        block = response.read1(65536)
                        if not block: break
                        size += len(block)
                        if size>MAX_WEB_BYTES: raise ValueError("Website download exceeds 64 MiB.")
                        output.write(block)
                if length and size != int(length): raise ValueError("Website download was interrupted; retry the source.")
                temporary.chmod(0o600)
                temporary.replace(destination)
                return {"url":url,"content_type":response.getheader("Content-Type",""),"bytes":size}
            finally: connection.close()
        raise ValueError("Website exceeded five redirects.")
    finally: temporary.unlink(missing_ok=True)
