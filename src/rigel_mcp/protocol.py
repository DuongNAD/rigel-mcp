"""The learning protocol: a plain Markdown file the learner owns and can edit at any time."""

from __future__ import annotations

from .config import Config

PROTOCOL_CAP = 1200  # characters re-sent with every search result

DEFAULT_PROTOCOL = """\
# Giao thức học
(Sửa file này để đổi cách AI giải thích. Giữ dưới ~1200 ký tự để lần tra cứu nào cũng được nhắc đủ.)

## Nguyên tắc
- Trả lời đúng câu hỏi trước (1–3 câu), rồi mới mở rộng. Không dẫn dắt vòng vo.
- Mọi ý chính bám tài liệu bài học và gắn [ref]. Ý ngoài tài liệu ghi "(ngoài tài liệu)".
- Học bằng làm: sau phần giải thích, đưa ngay một bài tập nhỏ hoặc đoạn code chạy được đúng khái niệm vừa hỏi.
- Dùng đúng thuật ngữ của tài liệu (giữ tiếng Anh gốc nếu tài liệu dùng tiếng Anh).
- Không giải thích lại thứ người học đã biết; chưa rõ mức độ thì hỏi 1 câu ngắn.

## Khung trả lời
1. Ý chính (1–3 câu) [ref]
2. Vì sao / hoạt động thế nào (ngắn, ví dụ lấy từ tài liệu) [ref]
3. Làm thử (5–10 phút)
4. Tự kiểm tra: 1 câu hỏi để người học tự trả lời trước khi xem đáp án
5. Nguồn: file · slide/trang đã dùng

## Khi tài liệu không đủ
Nói rõ phần nào tài liệu không có, rồi mới bổ sung và ghi "(nguồn ngoài)".
"""


def load_protocol(cfg: Config) -> str:
    """Return the protocol text, creating the default file on first use."""
    path = cfg.protocol_path
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_PROTOCOL, encoding="utf-8")
    return path.read_text(encoding="utf-8")


def protocol_reminder(text: str) -> str:
    text = text.strip()
    if len(text) <= PROTOCOL_CAP:
        return text
    cut = text[:PROTOCOL_CAP].rsplit("\n", 1)[0]
    return cut + "\n… (đã cắt cho gọn; bản đầy đủ ở resource rigel://protocol)"
