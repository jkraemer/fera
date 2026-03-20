"""Tests for the knowledge indexer daemon CLI."""

from fera.knowledge.daemon import build_parser


def test_parser_defaults():
    parser = build_parser()
    args = parser.parse_args(["/source", "/output"])
    assert args.watch_dir == "/source"
    assert args.output_dir == "/output"
    assert args.debounce == 2.0
    assert args.log_level == "INFO"
