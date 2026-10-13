<p align="center"><img src="docs/banner.jpg" alt="Rigel MCP — Rigel blazing at Orion's foot beside the Witch Head Nebula" width="100%"></p>

# rigel-mcp

Rigel, "bàn chân" của chòm Orion: câu trả lời đứng trên tài liệu bài học thật.

MCP server để hỏi–đáp và ôn tập **bám sát tài liệu bài học của bạn**. Thay vì để AI trả lời từ trí nhớ (dễ lan man, lệch bài), server ép một quy trình:

1. Tra tài liệu bài học trước (`search_material`).
2. Chỉ khẳng định điều có trong các đoạn tìm được, gắn nguồn `[D3#12]` (file · slide/trang/mục).
3. Tài liệu không đủ thì nói thẳng, rồi mới dùng nguồn ngoài và ghi nhãn.
4. Trả lời đúng trọng tâm, theo **giao thức học** của bạn (`protocol.md`).

Đã kiểm thử với `mcp` 2.3.0 (giao thức `2026-07-28`), Python 3.13 và Python 3.11 (Windows, bản 0.2.0). Chạy qua stdio nên dùng được với mọi client MCP (Claude Desktop, Claude Code, Codex, Antigravity...).

Từ bản 0.2.0, Rigel là lớp "bám tài liệu" của hệ sinh thái học tập: đọc thẳng các thư mục môn trong hub (qua [bản đồ thư viện](#bản-đồ-thư-viện-libraryjson)), tự khóa phạm vi theo file đang mở, đẩy thẻ ôn tập vào hàng đợi duy nhất của Vega và để lại bằng chứng buổi học cho Sirius (xem [Tích hợp hệ sinh thái](#tích-hợp-hệ-sinh-thái)).

## Cài đặt

Cần Python ≥ 3.11 (WSL + conda cũng được):

```bash
conda create -n rigel-mcp python=3.12 -y && conda activate rigel-mcp
cd rigel-mcp && pip install .
```

## Thư viện bài học

Tạo thư mục theo dạng `<thư viện>/<môn>/<bài>/<file>` (mặc định `~/rigel-library`):

```
~/rigel-library/
  PRJ301/
    L03-OOP/
      Lecture3.pptx
      Handout.docx
      lab/Lab3.md
```

Hỗ trợ `.pdf .pptx .docx .md .txt .rst .ipynb` và code (`.java .py .c .cpp .h .cs .js .ts .rs .go .sql .sh`). PPTX lấy cả ghi chú của slide; PDF dùng mục lục (bookmark) làm tiêu đề nếu có. PDF scan không có text sẽ được báo cảnh báo (chưa có OCR).

Đây là **chế độ thư mục**. Tài liệu đã nằm sẵn ở chỗ khác (ví dụ hub học tập trên SSD) thì không cần chép vào đây: dùng **bản đồ thư viện** bên dưới.

## Bản đồ thư viện (library.json)

Bản đồ khai báo **tài liệu đang nằm ở đâu** thay vì bắt bạn chép file vào một thư viện riêng. Đặt file tại `<RIGEL_HOME>/library.json`, hoặc trỏ biến `RIGEL_LIBRARY_MAP` tới nó. Mẫu đầy đủ ở [`examples/library.example.json`](examples/library.example.json).

```json
{
  "v": 1,
  "hub": "D:/02_Learning_Knowledge",
  "exclude": ["__pycache__", ".venv", "node_modules", ".git", ".ipynb_checkpoints", "polaris"],
  "maxFileMB": 40,
  "courses": [
    {
      "name": "GCI",
      "subject": "GCI_World_2026_September",
      "aliases": ["GCI World", "gci"],
      "roots": [
        { "path": "D:/02_Learning_Knowledge/GCI_World_2026_September/03_Materials", "lessons": "subfolders" },
        { "path": "D:/02_Learning_Knowledge/GCI_World_2026_September/roadmap/ROADMAP.md", "lessons": "file", "lesson": "_lo_trinh" },
        { "path": "D:/02_Learning_Knowledge/GCI_World_2026_September/06_Notes_Transcripts", "lessons": "single", "lesson": "Bai_giang" }
      ],
      "exclude": ["dap_an"]
    }
  ]
}
```

| Khóa | Ý nghĩa |
|---|---|
| `v` | Phiên bản định dạng, hiện là `1` (bắt buộc) |
| `hub` | Gốc hub học tập (tùy chọn): nơi Vega nhận thẻ (`<hub>/.vega/inbox/`) và chứa thư mục `subject` |
| `exclude` | Tên hoặc mẫu glob của MỘT thư mục/file, áp dụng cho mọi đoạn đường dẫn bên trong thư mục bài (không phân biệt hoa/thường). Bỏ khóa này thì mặc định là `__pycache__`, `.venv`, `venv`, `node_modules`, `.git`, `.ipynb_checkpoints` |
| `maxFileMB` | File lớn hơn bị bỏ qua và có cảnh báo (mặc định 40) |
| `courses[].name` | Tên môn dùng trong Rigel (duy nhất) |
| `courses[].subject` | Tên thư mục môn trong hub; cũng là `subject` của thẻ gửi Vega |
| `courses[].aliases` | Tên gọi khác mà `set_scope` chấp nhận (`gci`, `học máy`...) |
| `courses[].exclude` | Loại trừ riêng cho môn, cộng với `exclude` chung |
| `roots[].path` | Đường dẫn tuyệt đối của thư mục hoặc file |
| `roots[].lessons` | `subfolders`: mỗi thư mục con của `path` là một bài (file nằm ngay trong `path` thuộc bài `_chung`). `single`: cả thư mục là một bài tên `lesson` (mặc định là tên thư mục). `file`: một file là một bài tên `lesson` (mặc định là tên file không đuôi) |

Quy tắc chung:

- Tên ẩn (bắt đầu bằng `.` hoặc `~$`) luôn bị bỏ qua. `exclude` áp dụng cho thư mục bài và mọi đoạn đường dẫn bên dưới nó; thư mục con bị loại cũng không được duyệt tiếp.
- Mỗi lỗi trong file được báo bằng tiếng Việt kèm đường dẫn JSON của chỗ sai, ví dụ `courses[0].roots[1].lessons: giá trị không hợp lệ (đang là "folder"); cần: 'subfolders', 'single' hoặc 'file'`. Có file nhưng sai thì `sync_library` báo lỗi (không lặng lẽ quay về chế độ thư mục); không có file thì chế độ thư mục chạy y như cũ.
- `library_status` cho biết chế độ đang dùng, đường dẫn bản đồ, và với từng môn: các gốc, các bài, số file/đoạn.

### Cách tìm file (không quét đệ quy ổ D:)

Hub có hàng trăm nghìn file trên SSD exFAT nên Rigel không bao giờ quét đệ quy. Với mỗi bài, danh sách file là hợp của:

1. **Chỉ mục SmartDrive**: `smart-drive search --dir "<đường dẫn tương đối so với gốc ổ>" --ext <các đuôi hỗ trợ> --json --limit 5000` (timeout 30 giây). Mỗi gốc `subfolders` hỏi chỉ mục một lần rồi chia kết quả theo bài (mỗi lần gọi `smart-drive` mất khoảng nửa đến một giây trên máy này, nên hỏi riêng từng bài sẽ rất chậm khi hub có hàng trăm bài); gốc nào chạm giới hạn 5000 kết quả thì hỏi riêng từng bài, và có cảnh báo nếu một bài vẫn chạm giới hạn. Chỉ giữ kết quả nằm trong thư mục bài và không bị loại.
2. **Liệt kê không đệ quy** chính thư mục bài, để bắt file mới ở cấp ngoài cùng mà chỉ mục chưa thấy.

Không có SmartDrive, SmartDrive lỗi, hoặc thư mục không cùng ổ với gốc chỉ mục thì dùng **duyệt `os.scandir` có giới hạn**: sâu tối đa 4 cấp thư mục con, tối đa 3000 file mỗi bài, loại trước khi đi xuống, không đi theo symlink hay junction. Báo cáo của `sync_library` ghi rõ cách nào tìm được bao nhiêu file, và nhắc: `File mới ở thư mục con chưa có trong chỉ mục: chạy smart-drive update "<thư mục>" rồi sync_library.`

| Biến môi trường | Mặc định | Ý nghĩa |
|---|---|---|
| `RIGEL_SMARTDRIVE` | bật | `off`, `false`, `0` hoặc `no` thì tắt chỉ mục, chỉ dùng duyệt giới hạn |
| `RIGEL_SMARTDRIVE_CMD` | `smart-drive` trên PATH | Lệnh tìm kiếm (đường dẫn file thực thi, hoặc script `.py` chạy bằng chính Python của server) |
| `RIGEL_SMARTDRIVE_ROOT` | `D:/` | Gốc ổ mà đường dẫn trong chỉ mục tính từ đó |

### Đồng bộ và gỡ

- Mỗi tài liệu lưu `size`, `mtime` và `abs_path`. Lần `sync_library` sau, file có cùng size và mtime thì **không bị đọc lại**; chỉ khi khác mới tính sha256, và sha256 giống thì chỉ cập nhật size/mtime (không nạp lại, `ref` không đổi). CSDL tạo bởi bản 0.1.0 được nâng cấp tại chỗ.
- Tài liệu biến mất khỏi đĩa chỉ bị gỡ nếu nó thuộc đúng chế độ đang chạy: chế độ bản đồ chỉ gỡ tài liệu có nguồn `map`, chế độ thư mục chỉ gỡ tài liệu nguồn `library`; tài liệu nạp tay bằng `add_material` không bao giờ bị gỡ. Vì vậy tài liệu nạp từ chế độ thư mục cũ vẫn nằm trong chỉ mục sau khi chuyển sang bản đồ (`library_status` liệt kê chúng ở mục "Ngoài bản đồ" và đếm theo nguồn). Không nên xóa `study.db` để dọn: mọi `ref` đã ghi trong ghi chú, thẻ và fact sẽ đổi số.
- Thư mục nguồn không đọc được lúc đó (rút SSD, đổi tên thư mục...) thì tài liệu đã nạp từ đó được **giữ nguyên** và có cảnh báo, để `ref` (`D3#12`) trong ghi chú, thẻ và fact không bị xê dịch.
- Đổi tên hoặc di chuyển một file (khác môn, bài hoặc đường dẫn trong bài) được tính là gỡ file cũ và thêm file mới, nên `ref` của nó đổi. Sửa nội dung thì `ref` giữ nguyên.

### Khóa phạm vi

- `set_scope` nhận tên môn, tên thư mục `subject` hoặc bất kỳ bí danh nào (không phân biệt hoa/thường, dấu; `_`, `-` và khoảng trắng coi như nhau). Tên bài có thể là một phần tên nếu duy nhất.
- `scope_for_path { path }` nhận đường dẫn file hoặc thư mục người học đang làm việc, ví dụ `D:/02_Learning_Knowledge/Machine_Learning/02_Supervised_Learning/01_Regression/regression.py`, tìm môn có gốc (hoặc thư mục `subject`) chứa nó và bài chứa nó, rồi khóa như `set_scope`. File nằm trong thư mục môn nhưng ngoài mọi gốc thì khóa cả môn. Không khớp môn nào thì giữ nguyên phạm vi và liệt kê các môn. Trên Windows nhận cả đường dẫn kiểu Git Bash (`/d/...`) và WSL (`/mnt/d/...`).

## Cấu hình client

Cấu hình `mcpServers` chuẩn, dùng đường dẫn `python` của đúng môi trường conda:

```json
{
  "mcpServers": {
    "rigel": {
      "command": "/home/<bạn>/miniconda3/envs/rigel-mcp/bin/python",
      "args": ["-m", "rigel_mcp"],
      "env": {
        "RIGEL_LIBRARY": "/home/<bạn>/rigel-library",
        "RIGEL_NOTES": "/home/<bạn>/vault/study"
      }
    }
  }
}
```

| Biến môi trường | Mặc định | Ý nghĩa |
|---|---|---|
| `RIGEL_LIBRARY` | `~/rigel-library` | Thư mục tài liệu bài học (chế độ thư mục) |
| `RIGEL_HOME` | `~/.rigel` | `study.db`, `protocol.md`, `exports/`, `activity.jsonl`, `library.json` |
| `RIGEL_NOTES` | `<home>/notes` | Nơi `save_note` ghi (trỏ vào vault Obsidian nếu muốn) |
| `RIGEL_LIBRARY_MAP` | `<home>/library.json` | Bản đồ thư viện (xem trên) |
| `RIGEL_HUB` | `hub` trong bản đồ, rồi `D:/02_Learning_Knowledge` | Gốc hub; hộp thư Vega là `<hub>/.vega/inbox/` |

Client chạy trên Windows còn server nằm trong WSL: dùng `"command": "wsl.exe"` và `"args": ["-e", "env", "RIGEL_LIBRARY=/home/<bạn>/rigel-library", "/home/<bạn>/miniconda3/envs/rigel-mcp/bin/python", "-m", "rigel_mcp"]` (biến môi trường của client không tự đi vào WSL, nên đặt qua `env`). Dùng thư mục mặc định thì bỏ phần `env`.

## Dùng

Lần đầu: gọi `sync_library`, rồi `set_scope` (hoặc `scope_for_path`) để khóa vào bài đang học (ví dụ môn `PRJ301`, bài `L03`). Sau đó cứ hỏi bình thường; client sẽ tự tra tài liệu trước khi trả lời. Client nào hiển thị prompt MCP thì có thêm `learn`, `quiz`, `recap`.

| Tool | Việc |
|---|---|
| `library_status` | Xem chế độ (bản đồ/thư mục), môn/bài đã nạp, phạm vi đang khóa |
| `sync_library` | Nạp file mới/sửa, gỡ file đã xóa (theo bản đồ nếu có, so size/mtime rồi sha256) |
| `add_material` | Nạp một file nằm ngoài thư viện (không nhận thư mục ẩn) |
| `set_scope` | Khóa tìm kiếm vào môn/bài (nhận tên, `subject`, bí danh); `course="*"` để bỏ khóa |
| `scope_for_path` | Tự khóa phạm vi theo file/thư mục người học đang mở |
| `search_material` | Tra tài liệu: đoạn nguyên văn + `[ref]` + `coverage` + giao thức học |
| `read_chunks` | Đọc đoạn theo `ref`, kèm đoạn liền kề |
| `lesson_outline` | Mục lục bài (để tóm tắt, chọn phần đọc) |
| `sample_material` | Lấy ngẫu nhiên vài đoạn để ra câu hỏi ôn tập |
| `export_flashcards` | Xuất thẻ: `to="anki"` (mặc định) ra TSV, `to="vega"` ra hộp thư Vega, `to="both"` cả hai; thẻ không có `ref` hợp lệ bị từ chối |
| `save_note` | Lưu ghi chú Markdown kèm nguồn |

## Kết nối với MCP khác

MCP server không gọi nhau trực tiếp; client là bên điều phối. Rigel làm ba việc để các MCP khác cắm cạnh nó dễ dàng:

- **Quy tắc định tuyến** trong `instructions`: tài liệu bài học trước; thiếu thì mới dùng MCP tra docs/web và ghi nhãn "(nguồn ngoài)".
- **`ref` ổn định** (`D<tài liệu>#<đoạn>`, giữ nguyên khi bạn sửa file), để ghi chú và flashcard luôn truy ngược được nguồn.
- **Đầu ra dạng file**: TSV (có khai báo tab/HTML/cột tag) để nhập Anki; Markdown có front matter YAML để Obsidian, NotebookLM hoặc Filesystem MCP đọc.

## Tích hợp hệ sinh thái

### Vega: một hàng đợi ôn tập duy nhất

`export_flashcards { deck, cards, to: "vega" }` ghi **một file mỗi lần gọi** vào `<hub>/.vega/inbox/` theo hợp đồng `vega-card-inbox.v1` (bản bổ sung 2026-10-13: producer `rigel`, origin `rigel-ref`). Rigel không bao giờ ghi `cards.json` hay `reviews.jsonl` của Vega; Vega tự kiểm tra, loại trùng theo `key` và xếp lịch FSRS ở lần `vega_sync` kế tiếp (hoặc `vega_inbox { action: "ingest" }`).

- Tên file `rigel-<YYYYMMDDTHHmmss>-<6 hex>.jsonl`, UTF-8, mỗi dòng một thẻ. Ghi vào `<tên>.jsonl.tmp` rồi đổi tên nên Vega không bao giờ thấy file dở; file đã có không bao giờ bị ghi thêm hay ghi đè.
- Mỗi dòng gồm `v`, `t`, `producer: "rigel"`, `key`, `subject`, `deck`, `kind: "basic"`, `front`, `back`, `origin`, `tags`, `reason`:
  - `key` = `rigel:<subject hoặc môn>:<ref>:<8 hex đầu của sha1(front)>`, nên xuất lại cùng một thẻ không tạo thẻ mới.
  - `subject` = `subject` của môn trong bản đồ (không có thì tên môn). `back` = mặt sau + `\nNguồn: <file> · <vị trí>`.
  - `origin` = `{"kind": "rigel-ref", "path": <đường dẫn tuyệt đối của file nguồn>, "locator": "<ref> · <slide/trang/mục>"}`.
  - `tags` = môn, bài và thẻ của bạn (mỗi thẻ tối đa 40 ký tự, tối đa 10 thẻ). `reason` = `Thẻ từ tài liệu bài học (Rigel)`.
- Trước khi ghi, mỗi dòng đi qua bộ kiểm tra nghiêm ngặt theo hợp đồng (từ chối trường lạ, vượt giới hạn độ dài, `front` có emoji, `t` thiếu múi giờ). Thẻ không qua được bị liệt kê kèm lý do trong `vega_rejected`; thẻ không có `ref` hợp lệ vẫn bị từ chối như trước (`rejected`). `to="both"` vẫn ghi TSV cho những thẻ Anki chấp nhận.
- Thư mục hub phải tồn tại (Rigel không tạo hub); chỉ `.vega/inbox/` được tạo khi thiếu.

### Sirius: bằng chứng buổi học

Mỗi lần gọi thành công `search_material`, `read_chunks`, `lesson_outline`, `sample_material`, `export_flashcards`, `save_note` và `scope_for_path`, Rigel thêm một dòng vào `<RIGEL_HOME>/activity.jsonl`:

```json
{"v":1,"t":"2026-10-13T09:30:00+07:00","tool":"search_material","course":"GCI","lesson":"02_NumPy","subject":"GCI_World_2026_September","coverage":"strong","hits":4}
```

`coverage` chỉ có ở `search_material`, `cards` chỉ có ở `export_flashcards`. `hits` là số đoạn trả về (`lesson_outline`: số mục; `save_note`: số nguồn; `export_flashcards`: số đoạn nguồn khác nhau; `scope_for_path`: số file trong phạm vi). `course`, `lesson` lấy từ phạm vi đang dùng, `subject` từ bản đồ (không có thì `null`). Lần gọi lỗi không được ghi. **Không bao giờ ghi nội dung truy vấn, ghi chú hay thẻ**; lỗi khi ghi log không làm hỏng tool.

### Polaris và Aurora

Khi dựng fact Polaris hoặc nguồn slide Aurora từ tài liệu, dùng `[ref]` kèm vị trí (`file · slide/trang/mục`) làm locator bằng chứng: `ref` ổn định qua các lần `sync_library`, và kết quả tìm kiếm trả cả `path` tuyệt đối của file nguồn.

### Mira

Đoạn code tìm thấy trong tài liệu hiển thị kèm dòng `file: <đường dẫn tuyệt đối>` (và `path` trong kết quả có cấu trúc), nên khi bạn hỏi "đoạn này chạy thế nào" client có thể gọi `mira_record` trên đúng file để trace từng dòng.

### Dạy học

`instructions` yêu cầu client theo giao thức học đính kèm mỗi kết quả tìm kiếm và không viết lời giải bài tập thay bạn. Để giao thức đó mang thang gợi ý của bạn, sửa `protocol.md` (xem mục Giao thức học).

### Đăng ký qua Constellation

Server chạy bằng Python của venv, không cần `pip install` (đặt `PYTHONPATH` vào `src`). Mục `rigel` mẫu cho cấu hình của agent (đổi đường dẫn cho đúng máy bạn):

```json
{
  "mcpServers": {
    "rigel": {
      "command": "C:/Users/Admin/.venvs/rigel-mcp/Scripts/python.exe",
      "args": ["-B", "-m", "rigel_mcp"],
      "env": {
        "PYTHONPATH": "D:/03_Development_Projects/ai_agents/rigel_mcp/src",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "RIGEL_HOME": "C:/Users/Admin/.rigel",
        "RIGEL_LIBRARY_MAP": "C:/Users/Admin/.rigel/library.json",
        "RIGEL_HUB": "D:/02_Learning_Knowledge"
      }
    }
  }
}
```

Quy trình: `eco_import` để đưa cấu hình hiện có vào manifest, `eco_register { dry_run: true }` để xem trước, `eco_register` để áp dụng (có sao lưu), rồi `eco_health` kiểm tra kết nối stdio và `eco_doctor` kiểm tra lệch cấu hình giữa các agent. Hợp đồng `vega-card-inbox.v1` kiểm tra được bằng `eco_contracts`.

## Giao thức học

Lần chạy đầu tạo sẵn `~/.rigel/protocol.md` (nguyên tắc + khung trả lời, hướng "làm trước, lý thuyết sau"). Thay nội dung bằng giao thức của bạn; file được đọc lại mỗi lần tra cứu và gửi kèm kết quả `search_material` (tối đa 1200 ký tự) nên có hiệu lực ngay, không cần khởi động lại.

Gợi ý: ghi thang gợi ý của bạn vào đây (1 chỉ ra triệu chứng, 2 hỏi dẫn dắt, 3 phản ví dụ nhỏ, 4 pseudocode hoặc chữ ký hàm, 5 mã đầy đủ chỉ khi bạn yêu cầu rõ) để mọi agent đều theo cùng một cách dạy.

## Cách tìm kiếm hoạt động

- SQLite FTS5 + BM25, tiêu đề nhân trọng số 4. Một file `.db`, không cần tải model.
- Văn bản và truy vấn đều được bỏ dấu (`đ→d`) nên gõ "da hinh" vẫn tìm ra "Đa hình"; tiếng Anh dùng porter stemmer (`interfaces` ≈ `interface`).
- `coverage` = tỉ lệ từ khóa truy vấn thật sự có trong đoạn: `strong` ≥ 75%, `weak` ≥ 50%, thấp hơn thì coi là `none` và không trả đoạn nào.
- Tìm xuyên ngôn ngữ (hỏi tiếng Việt, tài liệu tiếng Anh): tool yêu cầu AI gửi truy vấn bằng ngôn ngữ của tài liệu, kèm bản dịch nếu chưa chắc.

## Giới hạn và bước tiếp theo

- Chưa có OCR (PDF scan) và chưa có embeddings; chỗ gắn là `Store.search`. Hướng nâng cấp: hybrid BM25 + vector (sqlite-vec, model đa ngữ như bge-m3).
- Code được cắt theo cửa sổ 60 dòng, chưa theo hàm/lớp.
- Chưa theo dõi "điểm yếu" qua các buổi học (ghi lại khái niệm hay sai để buổi sau ưu tiên).
- Chế độ bản đồ dùng chỉ mục SmartDrive nên file mới nằm ở thư mục con sâu chưa được `smart-drive update` sẽ chưa thấy (file mới ở cấp ngoài cùng của bài thì thấy ngay). Lần `sync_library` đầu trên hub lớn có thể lâu hơn thời gian chờ tool của một số client; server vẫn chạy tiếp việc đã bắt đầu và lưu từng tài liệu ngay khi nạp xong, hai lần gọi `sync_library` không chạy chồng nhau, nên gọi lại sau đó chỉ còn ít việc phải làm.
- `sync_library` chỉ hỗ trợ các đuôi file ở mục Thư viện bài học; ảnh, CSV và file nhị phân khác bị bỏ qua.

## Kiểm thử

```bash
pip install -e ".[dev]" && pytest
```

Trên Windows với venv của dự án (không tạo `__pycache__`):

```powershell
$env:PYTHONPATH="src"; $env:PYTHONDONTWRITEBYTECODE="1"; $env:PYTHONUTF8="1"
C:/Users/Admin/.venvs/rigel-mcp/Scripts/python.exe -B -m pytest -q -p no:cacheprovider
```

Bộ test dùng thư mục tạm cho mọi dữ liệu và tự tắt SmartDrive thật; các test chỉ mục dùng một lệnh giả (`RIGEL_SMARTDRIVE_CMD` trỏ tới script Python nhỏ in JSON). Hiện có 119 test: 12 test gốc của bản 0.1.0 (giữ nguyên) và 107 test mới. Test symlink tự bỏ qua khi tài khoản không đủ quyền tạo symlink (test junction vẫn chạy trên Windows).

## Thay đổi

### 0.2.0

- **Bản đồ thư viện** `library.json` (môn, `subject`, bí danh, gốc `subfolders`/`single`/`file`, `exclude` chung và riêng, `maxFileMB`), kiểm tra bằng pydantic với thông báo tiếng Việt kèm đường dẫn JSON. Chế độ thư mục cũ giữ nguyên.
- **Tìm file qua chỉ mục SmartDrive** + liệt kê không đệ quy, dự phòng duyệt `os.scandir` có giới hạn (4 cấp, 3000 file, không theo symlink/junction); báo cáo nêu cách tìm và nhắc `smart-drive update`.
- **Nhận diện thay đổi theo size/mtime** (không đọc lại file không đổi), cột `size`, `mtime`, `abs_path` (CSDL cũ tự nâng cấp), chỉ gỡ tài liệu thuộc đúng chế độ, giữ nguyên tài liệu của nguồn không đọc được.
- `set_scope` nhận `subject` và bí danh; tool mới **`scope_for_path`**; `library_status` hiển thị chế độ, bản đồ, gốc và bài của từng môn.
- **`export_flashcards`** có tham số `to` (`anki` | `vega` | `both`): ghi hộp thư Vega theo `vega-card-inbox.v1`, có bộ kiểm tra nghiêm ngặt, ghi nguyên tử.
- **Nhật ký hoạt động** `activity.jsonl` cho Sirius (không ghi nội dung).
- Kết quả tìm kiếm có `kind` và `path`; code hiển thị đường dẫn file để trace bằng Mira.
- `instructions` bổ sung khóa phạm vi trước khi tra, Vega, Mira, locator cho Polaris/Aurora và nguyên tắc dạy học; mẫu `.gitignore` và `examples/library.example.json`.

### 0.1.0

- Bản đầu: nạp thư viện `<môn>/<bài>/`, tìm kiếm FTS5 có `coverage`, giao thức học, xuất Anki, ghi chú, prompt `learn`/`quiz`/`recap`.
