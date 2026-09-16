from datetime import date

from distressed_equity.document_debt_extraction import NativePdfTextPayload
from distressed_equity.sec_instruments import FilingDocument
from distressed_equity import source_graph


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf(items: list[tuple[float, float, str]]) -> bytes:
    commands: list[str] = []
    for x, y, text in items:
        commands.extend([
            "BT", "/F1 10 Tf", f"1 0 0 1 {x:g} {y:g} Tm",
            f"({_pdf_escape(text)}) Tj", "ET",
        ])
    stream = ("\n".join(commands) + "\n").encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    parts = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(sum(len(part) for part in parts))
        parts.append(f"{number} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref = sum(len(part) for part in parts)
    parts.append(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    parts.append(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        parts.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    parts.append(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return b"".join(parts)


class _Response:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None


class _Session:
    def __init__(self, content: bytes):
        self.content = content

    def get(self, *_args, **_kwargs):
        return _Response(self.content)


class _Client:
    user_agent = "tests test@example.com"

    def __init__(self, content: bytes):
        self.session = _Session(content)

    def _throttle(self):
        return None


def test_sparse_structured_pdf_reaches_graph_bound_complex_extractor():
    content = _pdf([
        (40, 750, "Instrument"), (300, 750, "Principal ($ millions)"),
        (390, 750, "Maturity"), (500, 750, "CUSIP"),
        (40, 725, "Term Loan B"), (300, 725, "800"), (500, 725, "111111111"),
        (390, 700, "2028-12-15"),
    ])
    document = FilingDocument(
        sequence=2,
        description="Debt Schedule",
        document="notes.pdf",
        document_type="EX-4.1",
        size=len(content),
        url="https://www.sec.gov/Archives/example/notes.pdf",
        source_accession="0000000001-22-000001",
        filing_date=date(2022, 12, 1),
        filing_form="8-K",
    )
    payload = source_graph._get_text(_Client(content), document.url)
    assert isinstance(payload, NativePdfTextPayload)

    spans, candidates = source_graph.extract_source_candidates(
        payload,
        document,
        max_candidates=20,
    )
    groups = [candidate for candidate in candidates if candidate.cluster_status == "pdf_layout_row_group"]
    assert len(groups) == 1
    candidate = groups[0]
    assert candidate.snapshot_template["principal"] == 800_000_000
    assert candidate.snapshot_template["maturity_date"] == "2028-12-15"
    assert candidate.snapshot_template["cusip"] == "111111111"
    assert spans
