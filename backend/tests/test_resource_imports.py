import base64
import io
import json
import socket
import stat
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bughunter import import_formats
from bughunter.knowledge import KnowledgeService
from bughunter.store import Store


@pytest.fixture
def knowledge(tmp_path,monkeypatch):
    service = KnowledgeService(Store(tmp_path/"data"),start_worker=False)
    monkeypatch.setattr(service,"_embed",lambda texts:("test@1",[[1.,0.,1.] for _ in texts]))
    yield service
    service.shutdown()


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as archive:
        for name,data in files.items(): archive.writestr(name,data)
    return buffer.getvalue()


def ingest(service,name,raw):
    if isinstance(raw,str): raw = raw.encode()
    source = service.import_source(filename=name,content_base64=base64.b64encode(raw).decode())
    return service.process_source(source["id"])


def test_zip_parses_nested_files_with_provenance_and_partial_results(knowledge):
    raw = zipped({"course/notes.md":"Coupon eligibility must be validated.","examples/check.py":"# Price integrity lesson\nprint('this must never run')",
        "more.zip":zipped({"lesson.txt":"Use a second test account for authorization checks."}),"image.bin":b"\x00\xff\x01"})
    source = ingest(knowledge,"course.zip",raw)
    assert source["status"] == "partial"
    assert source["detected_format"] == "zip"
    assert knowledge.search("coupon")[0]["locator"] == "course/notes.md › Document"
    assert knowledge.search("second test account")[0]["locator"] == "more.zip › lesson.txt › Document"
    assert "this must never run" in knowledge.search("integrity")[0]["text"]
    assert len(source["import_items"]) == 4
    assert any(r["status"] == "error" and r["member"] == "image.bin" for r in source["import_items"])
    before = knowledge.get_source(source["id"])["chunk_count"]
    knowledge.retry_source(source["id"])
    assert knowledge.process_source(source["id"])["chunk_count"] == before
    artifacts = knowledge.store.root/"artifacts"/source["id"]
    knowledge.delete_source(source["id"])
    assert not artifacts.exists()
    assert knowledge.search("coupon") == []


@pytest.mark.parametrize("name",["../../escape.txt","/absolute.txt","C:/escape.txt","folder\\escape.txt"])
def test_zip_path_escape_is_rejected_before_extracting(knowledge,name):
    source = ingest(knowledge,"unsafe.zip",zipped({name:"do not write"}))
    assert source["status"] == "error"
    assert "unsafe member path" in source["error"]
    assert not (knowledge.store.root.parent/"escape.txt").exists()


def test_zip_symlinks_and_expansion_bombs_are_rejected(knowledge):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer,"w") as archive:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK|0o777)<<16
        archive.writestr(info,"/etc/passwd")
    assert "symbolic links" in ingest(knowledge,"link.zip",buffer.getvalue())["error"]
    bomb = ingest(knowledge,"bomb.zip",zipped({"huge.txt":"A"*(2*1024*1024)}))
    assert "compression-ratio" in bomb["error"]


def test_nested_archive_depth_is_bounded_and_reported(knowledge):
    raw = zipped({"end.txt":"not reached"})
    for i in range(4): raw = zipped({f"level{i}.zip":raw})
    source = ingest(knowledge,"deep.zip",raw)
    assert source["status"] == "partial"
    assert "three levels" in " ".join(source["warnings"])


@pytest.mark.parametrize("filename,raw",[("README",b"Coupon eligibility overview"),("example.py",b"import os\nos.system('THIS MUST NEVER EXECUTE')\n# Coupon rules"),("notes.dat","Coupon eligibility — हिंदी".encode("utf-16")),("legacy.txt","Coupon café".encode("cp1252"))])
def test_text_and_code_are_detected_without_extension_allowlist(knowledge,filename,raw):
    source = ingest(knowledge,filename,raw)
    assert source["status"] == "ready"
    assert knowledge.search("Coupon")


def test_utf8_probe_can_end_in_the_middle_of_a_character(knowledge):
    assert ingest(knowledge,"unicode.md","a"*8191+"é coupon")["status"] == "ready"


def test_malformed_executable_is_rejected_before_static_analysis(knowledge):
    source = ingest(knowledge,"sample.exe",b"MZ executable file")
    assert source["status"] == "error"
    assert "Truncated executable" in source["error"]


def test_word_paragraph_citations_and_macro_ignoring(knowledge):
    docx = zipped({"word/document.xml":'<w:document xmlns:w="word"><w:body><w:p><w:r><w:t>Coupon eligibility</w:t></w:r></w:p><w:p><w:r><w:t>Compare server totals</w:t></w:r></w:p></w:body></w:document>',"word/vbaProject.bin":b"never run"})
    source = ingest(knowledge,"lesson.docm",docx)
    assert source["status"] == "partial"
    assert knowledge.search("totals")[0]["locator"] == "Paragraph 2"
    assert "Macros were ignored" in source["warnings"][0]


def test_powerpoint_follows_slide_relationship_order_and_notes(knowledge):
    pptx = zipped({
        "ppt/presentation.xml":'<p:presentation xmlns:p="presentation" xmlns:r="relationships"><p:sldIdLst><p:sldId id="256" r:id="second"/><p:sldId id="257" r:id="first"/></p:sldIdLst></p:presentation>',
        "ppt/_rels/presentation.xml.rels":'<Relationships><Relationship Id="first" Target="slides/slide1.xml"/><Relationship Id="second" Target="slides/slide2.xml"/></Relationships>',
        "ppt/slides/slide1.xml":'<slide><p><t>Other topic</t></p></slide>',"ppt/slides/slide2.xml":'<slide><p><t>Coupon eligibility</t></p></slide>',
        "ppt/slides/_rels/slide2.xml.rels":'<Relationships><Relationship Id="notes" Target="../notesSlides/notesSlide2.xml"/></Relationships>',
        "ppt/notesSlides/notesSlide2.xml":'<notes><p><t>Compare the fixed pricing control.</t></p></notes>'})
    source = ingest(knowledge,"lesson.pptx",pptx)
    assert source["status"] == "partial"
    assert knowledge.search("coupon")[0]["locator"] == "Slide 1"
    assert knowledge.search("pricing")[0]["locator"] == "Slide 1 · Notes"


def test_spreadsheet_preserves_sheet_rows_and_does_not_evaluate_formula(knowledge):
    xlsx = zipped({"xl/workbook.xml":'<workbook xmlns:r="rels"><sheets><sheet name="Discounts" sheetId="1" r:id="sheet"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":'<Relationships><Relationship Id="sheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/sharedStrings.xml":'<sst><si><t>Coupon eligibility</t></si></sst>',
        "xl/worksheets/sheet1.xml":'<worksheet><sheetData><row r="4"><c r="A4" t="s"><v>0</v></c><c r="B4"><f>SUM(B1:B3)</f><v>10</v></c></row></sheetData></worksheet>'})
    source = ingest(knowledge,"rules.xlsx",xlsx)
    assert source["status"] == "partial"
    hit = knowledge.search("Coupon")[0]
    assert hit["locator"] == "Sheet Discounts · Row 4"
    assert "cached value only" in hit["text"]


def test_document_xml_external_entities_are_not_expanded(knowledge):
    raw = zipped({"word/document.xml":'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><document><p><t>&secret;</t></p></document>'})
    source = ingest(knowledge,"bad.docx",raw)
    assert source["status"] == "error"
    assert "entity declarations" in source["error"]


def test_image_inside_zip_has_registered_viewable_frame(knowledge,monkeypatch):
    from PIL import Image
    import pytesseract
    monkeypatch.setattr(pytesseract,"image_to_string",lambda *args,**kwargs:"Coupon exclusion diagram")
    buffer = io.BytesIO(); Image.new("RGB",(40,40),"white").save(buffer,format="PNG")
    source = ingest(knowledge,"slides.zip",zipped({"screens/demo.png":buffer.getvalue()}))
    frame = knowledge.list_frames(source["id"])[0]
    assert frame["locator"] == "screens/demo.png › Image"
    assert "member-" in frame["artifact_id"]
    assert Path(knowledge.get_frame(source["id"],frame["artifact_id"])["path"]).is_file()
    with pytest.raises(ValueError): knowledge.get_frame(source["id"],"../../secret.jpg")


@pytest.fixture
def site(monkeypatch):
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            hits.append((self.path,dict(self.headers)))
            if self.path in {"/redirect","/private","/loop"}:
                self.send_response(302)
                self.send_header("Location",{"/redirect":"/lesson","/private":"http://127.0.0.1/secret","/loop":"/loop"}[self.path]);self.end_headers();return
            if self.path == "/missing": self.send_response(404);self.end_headers();return
            data = zipped({"guide.md":"Coupon stacking lesson"}) if self.path == "/download.zip" else b'<html><head><title>Commerce notes</title></head><body><main><h1>Coupon eligibility</h1><p>Always compare the server total.</p></main><script>never_execute()</script></body></html>'
            self.send_response(200);self.send_header("Content-Length",str(len(data)));self.send_header("Content-Type","application/zip" if self.path.endswith("zip") else "text/html");self.end_headers();self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    original = import_formats.public_destination
    def validate(url):
        if url.startswith("http://public.example/"): return url,["93.184.216.34"]
        return original(url)
    monkeypatch.setattr(import_formats,"public_destination",validate)
    def connect(url,addresses):
        import http.client
        assert addresses == ["93.184.216.34"]
        return http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=3)
    monkeypatch.setattr(import_formats,"_connect",connect)
    yield hits
    server.shutdown();server.server_close()


def test_website_page_import_download_and_refresh(knowledge,site):
    queued = knowledge.import_source(url="http://public.example/redirect")
    source = knowledge.process_source(queued["id"])
    assert source["title"] == "Commerce notes"
    assert source["downloaded_url"] == "http://public.example/lesson"
    hit = knowledge.search("coupon")[0]
    assert hit["citation"]["url"] == "http://public.example/lesson"
    assert hit["retrieved_at"]
    assert "never_execute" not in hit["text"]
    assert [h[0] for h in site] == ["/redirect","/lesson"]
    assert all("Cookie" not in h[1] and "Authorization" not in h[1] for h in site)
    knowledge.retry_source(queued["id"])
    assert knowledge.process_source(queued["id"])["chunk_count"] == source["chunk_count"]
    download = knowledge.import_source(url="http://public.example/download.zip")
    assert knowledge.process_source(download["id"])["status"] == "ready"
    assert knowledge.search("stacking")[0]["locator"] == "guide.md › Document"


def test_website_redirect_to_private_address_is_never_requested(knowledge,site):
    source = knowledge.import_source(url="http://public.example/private")
    result = knowledge.process_source(source["id"])
    assert result["status"] == "error"
    assert "public destination" in result["error"]
    assert [h[0] for h in site] == ["/private"]


def test_website_redirect_limit_http_error_and_download_limit(knowledge,site,monkeypatch):
    for path,description in (("loop","redirects"),("missing","HTTP 404")):
        source = knowledge.import_source(url="http://public.example/"+path)
        assert description in knowledge.process_source(source["id"])["error"]
    monkeypatch.setattr(import_formats,"MAX_WEB_BYTES",10)
    source = knowledge.import_source(url="http://public.example/lesson")
    assert "exceeds" in knowledge.process_source(source["id"])["error"]


def test_web_dns_with_any_private_address_is_rejected(monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a,**k:[(2,1,6,"",("93.184.216.34",443)),(2,1,6,"",("127.0.0.1",443))])
    with pytest.raises(ValueError,match="public destination"): import_formats.public_destination("https://example.com")


def test_http_connection_uses_validated_ip_not_second_hostname_resolution(monkeypatch):
    seen = []
    monkeypatch.setattr(socket,"create_connection",lambda address,**kw:seen.append(address) or object())
    connection = import_formats._PinnedHTTP("example.com",80,timeout=2)
    connection.pinned_ip = "93.184.216.34"
    connection.connect()
    assert seen == [("93.184.216.34",80)]
