"""Lightweight source highlighting. Salt's renderer remains authoritative."""

from pygments.lexer import RegexLexer, bygroups
from pygments.token import Comment, Keyword, Name, Number, Operator, Punctuation, String, Text


class SaltSlsLexer(RegexLexer):
    name = "Salt SLS"
    aliases = ["salt-sls"]
    tokens = {
        "root": [
            (r"\{#.*?#\}", Comment),
            (r"\{%.*?%\}|\{\{.*?\}\}", String.Interpol),
            (r"^\s*(include|extend|exclude)(\s*:)", bygroups(Keyword.Namespace, Punctuation)),
            (r"^\s*(-\s*)?(require|require_in|watch|watch_in|onchanges|onchanges_in|onfail|onfail_in|prereq|prereq_in|use|use_in|listen|listen_in)(\s*:)", bygroups(Punctuation, Keyword, Punctuation)),
            (r"^\s*([\w.-]+)(\s*:)", bygroups(Name.Tag, Punctuation)),
            (r"\b(true|false|null|True|False|None)\b", Keyword.Constant),
            (r"\b\d+(?:\.\d+)?\b", Number),
            (r"#.*$", Comment.Single),
            (r"[\[\]{}(),:]", Punctuation),
            (r"[-+]", Operator),
            (r"[^\s{}#:\[\](),\n]+|[ \t]+", Text),
            (r"\n", Text),
        ]
    }
