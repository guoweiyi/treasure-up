from types import SimpleNamespace

from app.source_labels import apply_source_title, display_source_title


def test_unknown_source_names_do_not_present_identifiers_as_titles():
    source = SimpleNamespace(kind="creator", source_id="123", title="UP 123", monitor_state={})
    assert display_source_title(source) == "UP 主资料待获取"
    apply_source_title(source, "真实昵称")
    assert display_source_title(source) == "真实昵称"
    source.title = "自定义来源名称"
    apply_source_title(source, "更新后的昵称")
    assert display_source_title(source) == "自定义来源名称"
    assert source.monitor_state["source_title"] == "更新后的昵称"
