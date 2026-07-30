"""圧縮器のユニットテスト."""

from yt_live_kit.services.compressor import compress_lines


def test_compress_20_second_buckets():
    lines = [
        "[00:00:05] 最初の文",
        "[00:00:15] 同じバケット",
        "[00:00:25] 次のバケット",
    ]
    compressed = compress_lines(lines, bucket_seconds=20)

    assert len(compressed) == 2
    assert compressed[0].startswith("[00:00:00]")
    assert "最初の文" in compressed[0]
    assert "同じバケット" in compressed[0]
    assert compressed[1].startswith("[00:00:20]")
    assert "次のバケット" in compressed[1]


def test_compress_empty_lines_skipped():
    lines = [
        "[00:00:05] ",
        "[00:00:10] 有効なテキスト",
    ]
    compressed = compress_lines(lines)
    assert len(compressed) == 1
    assert "有効なテキスト" in compressed[0]


def test_compress_preserves_order():
    lines = [
        "[00:01:00] A",
        "[00:02:00] B",
        "[00:03:00] C",
    ]
    compressed = compress_lines(lines, bucket_seconds=20)
    assert len(compressed) == 3
    texts = [line.split("] ", 1)[1] for line in compressed]
    assert texts == ["A", "B", "C"]
