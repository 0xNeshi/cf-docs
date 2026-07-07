#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import re
from pathlib import Path


def clean_rst(text: str) -> str:
    # Remove comments/directives and RST markup while preserving human text.
    text = re.sub(r"^\.\.\s+_[^:]+:\s*$", "", text, flags=re.MULTILINE)  # anchors
    text = re.sub(r"^\.\.\s+code::.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\.\.\s+parsed-literal::.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\.\.\s+literalinclude::.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\.\.\s+note\s*::", "NOTE:", text, flags=re.MULTILINE)
    text = re.sub(r"^\.\.\s+warning\s*::", "WARNING:", text, flags=re.MULTILINE)
    text = re.sub(r"^\.\.\s+tip\s*::", "TIP:", text, flags=re.MULTILINE)
    text = re.sub(r"^\.\.\s+.*$", "", text, flags=re.MULTILINE)

    # Remove underline-only heading lines.
    text = re.sub(r"(?m)^[=\-~\"'`^*+#]{3,}\s*$", "", text)

    # RST inline links/roles.
    text = re.sub(r":ref:`([^`<]+)\s*<[^>]+>`", r"\1", text)
    text = re.sub(r":ref:`([^`]+)`", r"\1", text)
    text = re.sub(r":code:`([^`]+)`", r"\1", text)
    text = re.sub(r"`([^`<]+)\s*<[^>]+>`_", r"\1", text)

    # Normalize list markers.
    text = re.sub(r"(?m)^\s*#\.\s+", "- ", text)
    text = re.sub(r"(?m)^\s*\d+\.\s+", "- ", text)
    text = re.sub(r"(?m)^\s*[a-z]\.\s+", "- ", text)
    text = re.sub(r"(?m)^\s*[-*]\s+", "- ", text)

    # Unescape angle brackets from docs transforms if present.
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return text


def resolve_mdx_import(base_dir: Path, import_path: str) -> Path:
    if import_path.startswith("/"):
        return base_dir / import_path.removeprefix("/")
    return base_dir / import_path


def inline_imported_components(text: str, docs_main: Path) -> str:
    imports = dict(re.findall(r'^import\s+(\w+)\s+from\s+"([^"]+\.mdx)";\s*$', text, flags=re.MULTILINE))
    body = re.sub(r"(?m)^import .*$", "", text)
    for comp, import_path in imports.items():
        snippet_path = resolve_mdx_import(docs_main, import_path)
        if not snippet_path.exists():
            continue
        snippet_text = snippet_path.read_text(encoding="utf-8")
        # Remove imports inside snippets too, then inline once.
        snippet_text = re.sub(r"(?m)^import .*$", "", snippet_text).strip()
        body = re.sub(rf"<{re.escape(comp)}\s*/>", snippet_text, body)
    return body


def pick_network_tabs(text: str, network_title: str) -> str:
    def repl(match: re.Match[str]) -> str:
        block = match.group(0)
        tab_match = re.search(
            rf'<Tab title="{re.escape(network_title)}">\s*(.*?)\s*</Tab>',
            block,
            flags=re.DOTALL,
        )
        return tab_match.group(1).strip() if tab_match else ""

    return re.sub(
        r"\{/\*\s*NETWORKVARS_START.*?\*/\}.*?\{/\*\s*NETWORKVARS_END\s*\*/\}",
        repl,
        text,
        flags=re.DOTALL,
    )


def clean_mdx(text: str) -> str:
    # Drop frontmatter and imports (imports may have been already expanded).
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.DOTALL)
    text = re.sub(r"(?m)^import .*$", "", text)

    # Drop conversion markers.
    text = re.sub(r"\{/\*\s*COPIED_START.*?\*/\}", "", text)
    text = re.sub(r"\{/\*\s*COPIED_END\s*\*/\}", "", text)
    text = re.sub(r"\{/\*\s*NETWORKVARS_START.*?\*/\}", "", text)
    text = re.sub(r"\{/\*\s*NETWORKVARS_END\s*\*/\}", "", text)
    text = re.sub(r"(?m)^\s*>\s?", "", text)

    # Keep warning/note words, remove tags.
    text = re.sub(r"</?(Tabs|Tab)[^>]*>", "", text)
    text = re.sub(r"<Warning>", "WARNING:", text)
    text = re.sub(r"</Warning>", "", text)
    text = re.sub(r"<Note>", "NOTE:", text)
    text = re.sub(r"</Note>", "", text)
    text = re.sub(r"</?[^>]+>", "", text)

    # Normalize list markers.
    text = re.sub(r"(?m)^\s*\d+\.\s+", "- ", text)
    text = re.sub(r"(?m)^\s*[a-z]\.\s+", "- ", text)
    text = re.sub(r"(?m)^\s*[-*]\s+", "- ", text)

    # Markdown links and emphasis.
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("**", "").replace("*", "").replace("`", "")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return text


def normalize_units(text: str) -> list[str]:
    # Build robust comparison units (mostly sentences, plus code-like lines).
    compact_lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            compact_lines.append(line)

    joined = "\n".join(compact_lines)
    # Preserve code-like lines as standalone units.
    code_like = []
    prose_lines = []
    for line in joined.splitlines():
        if re.match(r"^(val |import |curl |kubectl |participant\.|Files\.|NOTE:|WARNING:|TIP:)", line):
            code_like.append(line)
        else:
            prose_lines.append(line)

    prose_text = " ".join(prose_lines)
    # sentence-ish splitting
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"<])", prose_text)
    units = [re.sub(r"\s+", " ", s).strip() for s in sentences if s.strip()]
    units.extend(code_like)
    return units


def canonicalize_dynamic_values(lines: list[str]) -> list[str]:
    canonical = []
    for line in lines:
        line = line.replace("|gsf_scan_url|", "SCAN_URL")
        line = re.sub(
            r"https://scan\.sv-1\.(dev|test)?\.?global\.canton\.network\.sync\.global",
            "SCAN_URL",
            line,
        )
        line = line.replace("https://scan.sv-1.unknown_cluster.global.canton.network.sync.global", "SCAN_URL")
        canonical.append(line)
    return canonical


def write_text(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare RST vs MDX as text-only lines.")
    parser.add_argument("--rst", required=True, help="Path to source RST file")
    parser.add_argument("--mdx", required=True, help="Path to target MDX file")
    parser.add_argument("--out-dir", required=True, help="Output directory")
    parser.add_argument("--name", default="comparison", help="Prefix for output files")
    parser.add_argument(
        "--network",
        default="mainnet",
        choices=["devnet", "testnet", "mainnet"],
        help="Tab content to compare for NETWORKVARS blocks",
    )
    args = parser.parse_args()

    rst_path = Path(args.rst)
    mdx_path = Path(args.mdx)
    out_dir = Path(args.out_dir)

    rst_lines = canonicalize_dynamic_values(normalize_units(clean_rst(rst_path.read_text(encoding="utf-8"))))

    mdx_raw = mdx_path.read_text(encoding="utf-8")
    docs_main = mdx_path.parents[2]  # docs-main
    mdx_inlined = inline_imported_components(mdx_raw, docs_main)
    network_title_map = {
        "devnet": "DevNet (0.6.4)",
        "testnet": "TestNet (0.6.3)",
        "mainnet": "MainNet (0.6.2)",
    }
    mdx_network_selected = pick_network_tabs(mdx_inlined, network_title_map[args.network])
    mdx_lines = canonicalize_dynamic_values(normalize_units(clean_mdx(mdx_network_selected)))

    rst_only = [l for l in rst_lines if l not in mdx_lines]
    mdx_only = [l for l in mdx_lines if l not in rst_lines]

    diff = list(
        difflib.unified_diff(
            rst_lines,
            mdx_lines,
            fromfile="rst-text-only",
            tofile="mdx-text-only",
            lineterm="",
            n=2,
        )
    )

    write_text(out_dir / f"{args.name}-rst-text-only.txt", rst_lines)
    write_text(out_dir / f"{args.name}-mdx-text-only.txt", mdx_lines)
    write_text(out_dir / f"{args.name}-text-only-unified.diff", diff)

    report = [
        f"# Text-only comparison: {args.name}",
        "",
        f"- RST file: `{rst_path}`",
        f"- MDX file: `{mdx_path}`",
        f"- NETWORKVARS tab selected: **{args.network}**",
        f"- RST text lines: **{len(rst_lines)}**",
        f"- MDX text lines: **{len(mdx_lines)}**",
        f"- Lines only in RST: **{len(rst_only)}**",
        f"- Lines only in MDX: **{len(mdx_only)}**",
        "",
        "## RST-only lines (first 80)",
        "",
    ]
    report.extend([f"- {l}" for l in rst_only[:80]] or ["- None"])
    report.extend(["", "## MDX-only lines (first 80)", ""])
    report.extend([f"- {l}" for l in mdx_only[:80]] or ["- None"])
    report.extend(
        [
            "",
            "## Diff file",
            "",
            f"- `{out_dir / f'{args.name}-text-only-unified.diff'}`",
        ]
    )
    write_text(out_dir / f"{args.name}-text-only-report.md", report)

    print(out_dir / f"{args.name}-text-only-report.md")


if __name__ == "__main__":
    main()
