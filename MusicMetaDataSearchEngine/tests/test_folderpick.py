from core import folderpick


def test_choose_folder_returns_trimmed_path():
    # osascript hängt ein '\n' an, ggf. trailing slash — beides sauber getrimmt
    out = folderpick.choose_folder(run=lambda cmd: "/Users/zzq/Music/Eingang\n")
    assert out == "/Users/zzq/Music/Eingang"


def test_choose_folder_cancel_returns_empty():
    # Abbruch: leere stdout
    assert folderpick.choose_folder(run=lambda cmd: "") == ""


def test_choose_folder_swallows_errors():
    def boom(cmd):
        raise OSError("osascript nicht da")
    assert folderpick.choose_folder(run=boom) == ""
