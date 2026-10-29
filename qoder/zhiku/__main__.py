"""命令行入口：python -m zhiku <命令>"""

from __future__ import annotations

import argparse
import json
import os


def main() -> None:
    ap = argparse.ArgumentParser(prog="zhiku", description="知库：本地资料混合检索（seekdb）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="把一个文件夹里的资料导入 seekdb")
    p.add_argument("dir", nargs="?", default="./sample_docs")

    p = sub.add_parser("search", help="命令行里直接检索")
    p.add_argument("q")
    p.add_argument("-k", type=int, default=5)
    p.add_argument("--tag")

    p = sub.add_parser("serve", help="启动网页界面")
    p.add_argument("--docs", default="./sample_docs")
    p.add_argument("--port", type=int, default=8000)

    p = sub.add_parser("verify", help="运行验证样例，生成 seekdb 检索记录")
    p.add_argument("--docs", default="./sample_docs")
    p.add_argument("--log", default="./docs/verify-log.md")

    p = sub.add_parser("export-demo", help="用真实检索结果生成项目网站的演示数据")
    p.add_argument("--out", default="./site/demo-data.js")

    args = ap.parse_args()

    if args.cmd == "serve":
        os.environ["ZHIKU_DOCS"] = args.docs
        import uvicorn

        uvicorn.run("zhiku.app:app", host="127.0.0.1", port=args.port)
        return

    from .store import Store

    store = Store()
    if args.cmd == "ingest":
        from .ingest import ingest_dir

        print(json.dumps(ingest_dir(store, args.dir), ensure_ascii=False))
    elif args.cmd == "search":
        for h in store.search(args.q, k=args.k, tag=args.tag):
            print(f"[{h.score:.4f}] {h.source} #{h.chunk}\n  {h.text[:120].replace(chr(10), ' ')}\n")
    elif args.cmd == "verify":
        from .verify import run

        raise SystemExit(0 if run(store, args.docs, args.log) else 1)
    elif args.cmd == "export-demo":
        from .demo import export

        export(store, args.out)
        print(f"已写入 {args.out}")


if __name__ == "__main__":
    main()
