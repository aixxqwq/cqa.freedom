"""Выгрузка results.json для организаторов.

Примеры (из папки backend):
    python export_results.py --group DEV --set dev --team "Название команды"
    python export_results.py --group FBroker --set prod --team "Название команды" --evaluate --expect 10

--evaluate  сначала оценивает диалоги набора, у которых ещё нет оценки ИИ
--refresh   заново скачать набор из API организаторов
Файл пишется в results/<set>/results.json в корне репозитория и сразу проверяется по правилам приложения.
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

from cqa import export, service  # noqa: E402
from cqa.datasource import fetch_group  # noqa: E402
from cqa.evaluator import provider  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    p = argparse.ArgumentParser(description="Выгрузка results.json по формату организаторов")
    p.add_argument("--group", required=True, help="group_id набора: DEV, FBroker, ...")
    p.add_argument("--set", required=True, choices=["dev", "prod"], help="папка results/dev или results/prod")
    p.add_argument("--team", required=True, help="название команды")
    p.add_argument("--checklist", default="default")
    p.add_argument("--evaluate", action="store_true", help="оценить неоценённые диалоги перед выгрузкой")
    p.add_argument("--refresh", action="store_true", help="заново скачать набор из API")
    p.add_argument("--expect", type=int, help="сколько записей должно быть (для prod — 10)")
    p.add_argument("--mode", default="external_api", choices=["local", "external_api", "hybrid"])
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    service.ensure_default_checklist()
    convs = fetch_group(a.group, refresh=a.refresh)
    print(f"Набор {a.group}: {len(convs)} диалогов")

    if a.evaluate:
        if provider() == "mock":
            print("ОШИБКА: не задан ключ ИИ в backend/.env — оценка была бы фиктивной (mock).")
            return 1
        have = export._latest_evaluations({c["conversation_id"] for c in convs})
        todo = [c["conversation_id"] for c in convs if c["conversation_id"] not in have]
        print(f"Оцениваю {len(todo)} диалогов через {provider()}…")

        def run(cid):
            try:
                ev = service.run_evaluation(a.group, cid, a.checklist)
                print(f"  ✓ {cid}: {ev['original']['total']}")
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ {cid}: {e}")

        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(run, todo))

    data = export.build_results(a.group, a.team, a.mode)
    out = ROOT / "results" / a.set / "results.json"
    export.write(data, out)
    ok = sum(r["status"] == "ok" for r in data["results"])
    print(f"Записано: {out}  (ok: {ok}, error: {len(data['results']) - ok}, модель: {data['model']['name']})")

    problems = export.validate(data, a.expect)
    if problems:
        print("ПРОБЛЕМЫ:")
        for x in problems:
            print("  -", x)
        return 1
    print("Проверка формата пройдена ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
