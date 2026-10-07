"""A fastapi-antd component that `static/index.html` never loads fails the check (#684).

Live (2026-10-07, prompt 9b): `productpage.js` rendered `window.app.AskReviewSection`, defined in
`static/components/AskReview.js`, and `index.html` had no `<script>` for that file. Every script
parsed, so the syntax check passed, and Product insights crashed on open with React error #130
(an undefined component). The page runs only the scripts `index.html` lists, so a `window.<ns>.X`
that one script reads and another defines needs the defining one listed there.
"""
from __future__ import annotations

from pathlib import Path

from sage.feedback.runner import FeedbackRunner

INDEX = """\
<!DOCTYPE html>
<html><body>
  <div id="root"></div>
  <script src="static/vendor/react.production.min.js"></script>
{extra}  <script src="static/productpage.js"></script>
  <script src="static/app.js"></script>
</body></html>
"""


def _app(tmp_path: Path, *, extra: str) -> Path:
    (tmp_path / ".sage").mkdir()
    (tmp_path / ".sage" / "settings.json").write_text('{"stack": "fastapi-antd"}')
    (tmp_path / "static" / "components").mkdir(parents=True)
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "static" / "index.html").write_text(INDEX.format(extra=extra))
    (tmp_path / "static" / "components" / "AskReview.js").write_text(
        "window.app = window.app || {};\n"
        "window.app.AskReviewSection = function AskReviewSection() { return null; };\n")
    (tmp_path / "static" / "productpage.js").write_text(
        "window.app = window.app || {};\n"
        "window.app.ProductPage = function ProductPage() {\n"
        "  return React.createElement(window.app.AskReviewSection, null);\n"
        "};\n")
    (tmp_path / "static" / "app.js").write_text(
        "const h = React.createElement;\n"
        "ReactDOM.createRoot(document.getElementById('root')).render(h(window.app.ProductPage));\n")
    return tmp_path


def test_a_component_with_no_script_tag_fails_naming_its_file(tmp_path: Path):
    report = FeedbackRunner().check(_app(tmp_path, extra=""))

    assert not report.ok
    assert [(e.file, e.code) for e in report.errors] == [("static/index.html", "SAGE002")]
    assert "static/components/AskReview.js" in report.errors[0].message
    assert "window.app.AskReviewSection" in report.errors[0].message


def test_a_skill_helper_namespace_with_no_script_tag_fails_naming_its_file(tmp_path: Path):
    app = _app(tmp_path, extra='  <script src="static/components/AskReview.js"></script>\n')
    (app / "static" / "dealDesk.js").write_text(
        "window.dealDesk = { score: function score(deal) { return deal.amount; } };\n")
    (app / "static" / "productpage.js").write_text(
        "window.app = window.app || {};\n"
        "window.app.ProductPage = function ProductPage() {\n"
        "  return React.createElement(window.app.AskReviewSection, { score: window.dealDesk.score });\n"
        "};\n")

    report = FeedbackRunner().check(app)

    assert [(e.file, e.code) for e in report.errors] == [("static/index.html", "SAGE002")]
    assert "static/dealDesk.js defines window.dealDesk.score" in report.errors[0].message


def test_a_reader_the_page_never_loads_is_not_reported(tmp_path: Path):
    # Two leftover files `index.html` no longer lists: the read never runs, so nothing crashes.
    app = _app(tmp_path, extra='  <script src="static/components/AskReview.js"></script>\n')
    (app / "static" / "components" / "Gone.js").write_text(
        "window.app.GoneSection = function GoneSection() { return null; };\n")
    (app / "static" / "components" / "Old.js").write_text(
        "window.app.OldPage = function OldPage() {\n"
        "  return React.createElement(window.app.GoneSection, null);\n"
        "};\n")

    report = FeedbackRunner().check(app)

    assert report.ok, report.as_agent_message()


def test_the_same_app_with_the_tag_passes(tmp_path: Path):
    report = FeedbackRunner().check(_app(
        tmp_path, extra='  <script src="static/components/AskReview.js"></script>\n'))

    assert report.ok, report.as_agent_message()
