from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A5, landscape
from reportlab.pdfgen import canvas

ROOT = Path(__file__).parent
PAGE = A5
DOUBLE = landscape(A5)


def _page(
    pdf: canvas.Canvas,
    title: str,
    body: str | None,
    printed: str | None,
    *,
    double: tuple[tuple[str, str], tuple[str, str]] | None = None,
) -> None:
    width, height = pdf._pagesize
    pdf.setFillColorRGB(0.07, 0.1, 0.15)
    pdf.rect(0, 0, width, height, fill=1, stroke=0)
    pdf.setFillColorRGB(0.48, 0.68, 0.95)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(34, height - 48, title)
    if double:
        pdf.setStrokeColorRGB(0.35, 0.45, 0.6)
        pdf.line(width / 2, 28, width / 2, height - 28)
        for index, (heading, text) in enumerate(double):
            offset = index * width / 2
            pdf.setFillColorRGB(0.93, 0.96, 0.99)
            pdf.setFont("Helvetica-Bold", 15)
            pdf.drawString(offset + 32, height - 92, heading)
            pdf.setFont("Helvetica", 11)
            pdf.drawString(offset + 32, height - 118, text)
    elif body is not None:
        pdf.setFillColorRGB(0.93, 0.96, 0.99)
        pdf.setFont("Helvetica", 11)
        for line_index, line in enumerate(body.splitlines()):
            pdf.drawString(34, height - 88 - line_index * 17, line)
    if printed:
        pdf.setFillColorRGB(0.65, 0.72, 0.82)
        pdf.setFont("Helvetica", 9)
        pdf.drawCentredString(width / 2, 20, printed)
    pdf.showPage()


def _write(name: str, pages: list[dict[str, object]], *, page_size=PAGE) -> None:
    target = ROOT / name
    pdf = canvas.Canvas(
        str(target),
        pagesize=page_size,
        pageCompression=1,
        invariant=1,
    )
    pdf.setAuthor("DeutschOS synthetic test fixture")
    pdf.setTitle(name)
    for page in pages:
        _page(pdf, **page)
    pdf.save()


def _ocr_layer(name: str, hidden_text: str) -> None:
    pdf = canvas.Canvas(str(ROOT / name), pagesize=PAGE, pageCompression=1, invariant=1)
    width, height = PAGE
    pdf.setFillColorRGB(0.07, 0.1, 0.15)
    pdf.rect(0, 0, width, height, fill=1, stroke=0)
    pdf.setFillColorRGB(0.48, 0.68, 0.95)
    pdf.rect(36, height - 155, width - 72, 82, fill=1, stroke=0)
    pdf.setFillColorRGB(0.93, 0.96, 0.99)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(54, height - 112, "Identischer Render")
    text = pdf.beginText(34, height - 190)
    text.setTextRenderMode(3)
    text.textLine(hidden_text)
    pdf.drawText(text)
    pdf.showPage()
    pdf.save()


def _image_only(name: str) -> None:
    pdf = canvas.Canvas(str(ROOT / name), pagesize=PAGE, pageCompression=1, invariant=1)
    width, height = PAGE
    pdf.setFillColorRGB(0.07, 0.1, 0.15)
    pdf.rect(0, 0, width, height, fill=1, stroke=0)
    pdf.setFillColorRGB(0.48, 0.68, 0.95)
    pdf.circle(width / 2, height / 2, 96, fill=1, stroke=0)
    pdf.setFillColorRGB(0.93, 0.96, 0.99)
    pdf.rect(width / 2 - 54, height / 2 - 28, 108, 56, fill=1, stroke=0)
    pdf.showPage()
    pdf.save()


def generate() -> None:
    _write(
        "fixture_a_base.pdf",
        [
            {"title": f"Seite {number}", "body": body, "printed": str(number)}
            for number, body in enumerate(
                ["Kapitel eins", "Gruesse aus Koeln", "Uebung drei", "Loesung vier"],
                1,
            )
        ],
    )
    _write(
        "fixture_a_target.pdf",
        [
            {"title": f"Seite {number}", "body": body, "printed": str(number)}
            for number, body in enumerate(
                ["Kapitel eins.", "Gruesse aus Koeln!", "Uebung drei", "Loesung vier"],
                1,
            )
        ],
    )
    _write(
        "fixture_b_base.pdf",
        [
            {"title": "Umschlag", "body": "Synthetischer Test", "printed": None},
            {
                "title": "Doppelblatt 1-2",
                "body": None,
                "printed": None,
                "double": (("Seite 1", "Links"), ("Seite 2", "Rechts")),
            },
            {
                "title": "Doppelblatt 3-4",
                "body": None,
                "printed": None,
                "double": (("Seite 3", "Links"), ("Seite 4", "Rechts")),
            },
            {"title": "Rueckseite", "body": "Ende", "printed": None},
        ],
        page_size=DOUBLE,
    )
    _write(
        "fixture_b_target.pdf",
        [
            {"title": "Umschlag", "body": "Synthetischer Test", "printed": None},
            *[
                {"title": f"Seite {number}", "body": side, "printed": str(number)}
                for number, side in enumerate(["Links", "Rechts", "Links", "Rechts"], 1)
            ],
            {"title": "Rueckseite", "body": "Ende", "printed": None},
        ],
    )
    _write(
        "fixture_c_base.pdf",
        [
            {"title": title, "body": "Basis", "printed": str(index)}
            for index, title in enumerate(["Alpha", "Entfernt", "Gamma"], 1)
        ],
    )
    _write(
        "fixture_c_target.pdf",
        [
            {"title": title, "body": "Ziel", "printed": str(index)}
            for index, title in enumerate(["Alpha", "Eingefuegt", "Gamma"], 1)
        ],
    )
    _write(
        "fixture_d_base.pdf",
        [
            {"title": title, "body": "Reihenfolge", "printed": str(index)}
            for index, title in enumerate(["A", "B", "C", "D"], 1)
        ],
    )
    _write(
        "fixture_d_target.pdf",
        [
            {"title": title, "body": "Reihenfolge", "printed": str(index)}
            for index, title in enumerate(["A", "C", "B", "D"], 1)
        ],
    )
    _ocr_layer("fixture_e_base.pdf", "Gr\ufffdße aus Koln. Wör-ter.")
    _ocr_layer("fixture_e_target.pdf", "Grüße aus Köln. Wörterx.")
    _image_only("fixture_f_base.pdf")
    _image_only("fixture_f_target.pdf")
    _write(
        "fixture_g_base.pdf",
        [{"title": "Aehnlich", "body": "Mehrdeutig", "printed": "1"}],
    )
    _write(
        "fixture_g_target.pdf",
        [
            {"title": "Aehnlich", "body": "Mehrdeutig", "printed": "1"},
            {"title": "Aehnlich", "body": "Mehrdeutig", "printed": "2"},
        ],
    )


if __name__ == "__main__":
    generate()
