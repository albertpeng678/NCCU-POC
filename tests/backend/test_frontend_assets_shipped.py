# tests/backend/test_frontend_assets_shipped.py
"""回歸守門：index.html / app.js 引用的每個前端資產，都必須 (1) 存在於 frontend/、
(2) 被 backend/Dockerfile 納入映像（整包 COPY 或列舉涵蓋）。

防 Session 8 的線上停機重演：Dockerfile 寫死的 `COPY frontend/<列舉>` 漏掉 Session 7 新增的
shiba-progress.js / qa-recovery.js / end-state.js / shiba.json → 線上 404 → app.js 的 ESM
import 失敗 → 全站死。此測試對「舊列舉式 Dockerfile」會 FAIL、對「整包 COPY」會 PASS。
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND = _ROOT / "frontend"
_DOCKERFILE = _ROOT / "backend" / "Dockerfile"


def _referenced_assets() -> set[str]:
    """收集 index.html + app.js 引用的本地前端資產檔名（去掉 ?v= 與路徑、排除 CDN）。"""
    refs: set[str] = set()
    html = (_FRONTEND / "index.html").read_text(encoding="utf-8")
    appjs = (_FRONTEND / "app.js").read_text(encoding="utf-8")

    # index.html: <script src="app.js?v=34"> / <link href="style.css?v=34">（排除 https:// CDN）
    for m in re.finditer(r'(?:src|href)\s*=\s*["\']([^"\']+\.(?:js|css))(?:\?[^"\']*)?["\']', html):
        url = m.group(1)
        if "//" not in url:                       # 跳過 CDN 絕對網址
            refs.add(Path(url).name)

    # app.js: import ... from "./x.js?v=34" / fetch(`shiba.json?v=34`)（含模板字面/單雙引號）
    for m in re.finditer(r'from\s+["\']\.?/?([\w.\-/]+\.(?:js|json|css))(?:\?[^"\']*)?["\']', appjs):
        refs.add(Path(m.group(1)).name)
    for m in re.finditer(r'fetch\(\s*[`"\']\.?/?([\w.\-/]+\.(?:js|json|css))(?:\?[^`"\']*)?[`"\']', appjs):
        refs.add(Path(m.group(1)).name)
    return refs


def _shipped_assets() -> set[str]:
    """Dockerfile 會放進映像的 frontend 檔名集合。整包 COPY → 回傳 frontend/ 全部檔名。"""
    text = _DOCKERFILE.read_text(encoding="utf-8")
    shipped: set[str] = set()
    for line in text.splitlines():
        s = line.strip()
        if not s.upper().startswith("COPY") or "frontend" not in s:
            continue
        tokens = s.split()[1:]                     # 去掉 COPY，最後一個是目的地
        sources = tokens[:-1]
        for src in sources:
            # 整包：`frontend/` 或 `frontend` → 視為涵蓋全部 frontend 檔
            if src in ("frontend/", "frontend", "./frontend/", "./frontend"):
                return {p.name for p in _FRONTEND.iterdir() if p.is_file()}
            if src.startswith("frontend/"):
                shipped.add(Path(src).name)
    return shipped


def test_referenced_frontend_assets_exist_on_disk():
    missing = sorted(a for a in _referenced_assets() if not (_FRONTEND / a).exists())
    assert not missing, f"index.html/app.js 引用了不存在的前端檔：{missing}"


def test_dockerfile_ships_every_referenced_asset():
    referenced = _referenced_assets()
    shipped = _shipped_assets()
    not_shipped = sorted(referenced - shipped)
    assert not not_shipped, (
        f"backend/Dockerfile 沒把這些被引用的前端資產放進映像（→ 線上 404 → app.js 全站死）：{not_shipped}。"
        f"請改用整包 `COPY frontend/ ./frontend/` 或把缺檔加進 COPY 清單。"
    )
