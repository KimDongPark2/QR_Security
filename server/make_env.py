"""`.env` 파일을 만들고 비어 있는 키(JWT_SECRET, PII_KEY, ADMIN_TOKEN)를 자동으로 채운다.

사용법 (server 폴더에서):  python make_env.py
- 키는 이 컴퓨터에서 무작위로 만들어지고 화면에 출력하지 않는다.
- 이미 채워진 키는 바꾸지 않는다(PII_KEY가 바뀌면 기존 암호문을 풀 수 없기 때문).
"""
import base64
import secrets
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLE = HERE / ".env.example"
ENV = HERE / ".env"
KEYS = ("JWT_SECRET", "PII_KEY", "ADMIN_TOKEN")


def new_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def main() -> None:
    source = ENV if ENV.exists() else EXAMPLE
    lines = source.read_text(encoding="utf-8-sig").splitlines()
    filled, kept = [], []
    out = []
    for line in lines:
        name, sep, value = line.partition("=")
        name = name.strip()
        if sep and name in KEYS and not line.lstrip().startswith("#"):
            if value.strip():
                kept.append(name)
            else:
                line = f"{name}={new_key()}"
                filled.append(name)
        out.append(line)
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(".env 준비 완료")
    if filled:
        print("  새로 채운 키:", ", ".join(filled))
    if kept:
        print("  이미 있어서 그대로 둔 키:", ", ".join(kept))
    missing = [k for k in KEYS if k not in filled + kept]
    if missing:
        print("  [주의] .env에 이 줄이 없어요:", ", ".join(missing))


if __name__ == "__main__":
    main()
