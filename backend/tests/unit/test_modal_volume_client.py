"""modal CLI 输出 quirk 修复：volume ls --json 对非 ASCII 文件名的双重转义还原。"""

from app.modal_volume.client import _unescape_cli_name


def test_unescapes_double_escaped_chinese():
    # CLI 实测输出：json.loads 一次后名字仍是字面 backslash-u 序列
    assert _unescape_cli_name("krea2-\\u6587\\u751f\\u56fe.json") == "krea2-文生图.json"


def test_keeps_plain_ascii_and_real_chinese():
    assert _unescape_cli_name("plain-node.json") == "plain-node.json"
    assert _unescape_cli_name("krea2-文生图.json") == "krea2-文生图.json"


def test_keeps_invalid_sequences_and_lone_surrogates():
    # 非 hex 序列不动
    assert _unescape_cli_name("weird-\\uzzzz.json") == "weird-\\uzzzz.json"
    # 代理对拆出的孤立代理项无法编码 utf-8 → 保留原样，不产生坏字符串
    lone = "emoji-\\ud83d.json"
    assert _unescape_cli_name(lone) == lone
