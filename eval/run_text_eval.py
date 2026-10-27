"""Text evaluation of the understanding + triage pipeline (SPEC 11).

    python eval/run_text_eval.py --llm none|mock|ollama [--cases eval/text_cases.jsonl]

Writes eval/results/text_eval.md + .json (with --llm mock/ollama: text_eval_<llm>.md/.json).
Reports need precision/recall, field accuracy, emergency accuracy, dedupe precision/recall,
latency, P1 recall AND precision, and the must-be / must-not-be P1 cases.
Exit code 1 if rules-only (--llm none) P1 recall < 100% or a must case fails.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkshelter import codebook, extract  # noqa: E402
from darkshelter.dedupe import find_candidates  # noqa: E402

DEFAULT_NOW = "2026-10-28T10:00"


def parse_now(s):
    return time.mktime(time.strptime(s or DEFAULT_NOW, "%Y-%m-%dT%H:%M"))


def load_cases(path):
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def make_llm(kind, config_path=None):
    if kind == "none":
        return None
    try:
        from darkshelter import llm as llm_mod
    except Exception as e:  # the module is written by another part of the build
        sys.exit(f"--llm {kind}: darkshelter.llm is not importable ({e!r})")
    if kind == "mock":
        try:
            return llm_mod.MockLLM(delay=0.0)
        except TypeError:
            return llm_mod.MockLLM()
    from darkshelter.config import load_config
    cfg = load_config(config_path, ensure_key=False)
    if hasattr(llm_mod.OllamaLLM, "from_config"):
        llm = llm_mod.OllamaLLM.from_config(cfg)
    else:
        c = cfg["llm"]
        llm = llm_mod.OllamaLLM(c["url"], c["model"], c["timeout_s"], c["num_predict"], c["keep_alive"])
    if hasattr(llm, "available") and not llm.available():
        sys.exit(f"--llm ollama: model {cfg['llm']['model']} is not available at {cfg['llm']['url']}")
    return llm


def gold_needs(case):
    out = []
    for g in case["gold"]["needs"]:
        out.append({"category": codebook.cat_code(g["category"]), "item_code": int(g.get("item_code") or 0),
                    "quantity": g.get("quantity"), "zone": int(g.get("zone") or 0),
                    "priority": int(g["priority"])})
    return out


def match(pred, gold):
    """Greedy: each gold need takes the first unmatched prediction with the same category and item."""
    used, pairs = set(), []
    for gi, g in enumerate(gold):
        for pi, p in enumerate(pred):
            if pi in used or p.category != g["category"]:
                continue
            if g["item_code"] and p.item_code != g["item_code"]:
                continue
            used.add(pi)
            pairs.append((gi, pi))
            break
    return pairs


def pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def percentile(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, int(round(q / 100.0 * (len(xs) - 1)))))
    return round(xs[k], 2)


def emergency_set(s):
    return {x for x in (s or "").split(",") if x}


def evaluate(cases, llm=None):
    rows = []
    totals = {"pred": 0, "gold": 0, "matched": 0, "item_ok": 0, "qty_ok": 0, "zone_ok": 0, "prio_ok": 0,
              "cat_hit": 0, "emerg_ok": 0, "gold_p1": 0, "p1_recalled": 0, "pred_p1": 0, "p1_precise": 0,
              "llm_errors": 0, "llm_fields": 0, "ungrounded": 0}
    lat_rules, lat_llm, lat_total = [], [], []
    failures = []
    pool = []          # dedupe pool: (case_id, [ticket dicts])
    dd = {"tp": 0, "fp": 0, "fn": 0}
    for case in cases:
        now = parse_now(case.get("now"))
        text = case["text"]
        t0 = time.perf_counter()
        needs = extract.analyze(text, now)
        t1 = time.perf_counter()
        llm_info = None
        if llm is not None:
            res = llm.extract(text)
            if res.get("error"):
                totals["llm_errors"] += 1
                llm_info = {"error": res.get("error")}
            else:
                needs = extract.refine(needs, res.get("needs") or [], text, now)
                llm_info = {"needs": len(res.get("needs") or []), "tokens_per_s": res.get("tokens_per_s")}
            for n in needs:
                for k, v in (n.field_sources or {}).items():
                    if v in ("llm", "llm_ungrounded"):
                        totals["llm_fields"] += 1
                        if v == "llm_ungrounded":
                            totals["ungrounded"] += 1
        t2 = time.perf_counter()
        lat_rules.append((t1 - t0) * 1000)
        if llm is not None:
            lat_llm.append((t2 - t1) * 1000)
        lat_total.append((t2 - t0) * 1000)

        gold = gold_needs(case)
        pairs = match(needs, gold)
        totals["pred"] += len(needs)
        totals["gold"] += len(gold)
        totals["matched"] += len(pairs)
        for gi, pi in pairs:
            g, p = gold[gi], needs[pi]
            totals["item_ok"] += int(p.item_code == g["item_code"])
            gq = g["quantity"] or None
            totals["qty_ok"] += int((p.quantity or None) == gq)
            totals["zone_ok"] += int(int(p.zone_code or 0) == g["zone"])
            totals["prio_ok"] += int(p.priority == g["priority"])
        pred_cats = [n.category for n in needs]
        totals["cat_hit"] += sum(1 for g in gold if g["category"] in pred_cats)
        pe = set()
        for n in needs:
            pe |= emergency_set(n.emergency)
        ge = emergency_set(case["gold"].get("emergency"))
        totals["emerg_ok"] += int(pe == ge)

        # P1 recall / precision (need level, by category)
        gold_p1 = [g for g in gold if g["priority"] == 1]
        pred_p1 = [n for n in needs if n.priority == 1]
        recalled = [g for g in gold_p1 if any(n.category == g["category"] or "unclassified_red_flag" in n.flags
                                              for n in pred_p1)]
        precise = [n for n in pred_p1 if any(g["category"] == n.category for g in gold_p1)
                   or ("unclassified_red_flag" in n.flags and gold_p1)]
        totals["gold_p1"] += len(gold_p1)
        totals["p1_recalled"] += len(recalled)
        totals["pred_p1"] += len(pred_p1)
        totals["p1_precise"] += len(precise)

        tags = case.get("tags") or []
        ok = True
        if "must_p1" in tags and len(recalled) < len(gold_p1):
            ok = False
            failures.append({"id": case["id"], "why": "must be P1", "got": describe(needs)})
        if "must_not_p1" in tags and pred_p1:
            ok = False
            failures.append({"id": case["id"], "why": "must not be P1", "got": describe(needs)})

        # dedupe pool (file order)
        if "dedupe" in tags:
            tickets = []
            for i, n in enumerate(needs):
                tickets.append({"seq": f"{case['id']}#{i}", "category": n.category, "item_code": n.item_code,
                                "zone_code": n.zone_code, "location": n.location, "subject": n.subject,
                                "summary": n.summary, "created_wall": now, "status": "draft"})
            predicted = set()
            for tk in tickets:
                open_t = [t for _cid, ts in pool for t in ts]
                for cand in find_candidates(tk, open_t, now):
                    predicted.add(str(cand["seq"]).split("#")[0])
            gold_dup = case["gold"].get("dup_of")
            gold_set = {gold_dup} if gold_dup else set()
            dd["tp"] += len(predicted & gold_set)
            dd["fp"] += len(predicted - gold_set)
            dd["fn"] += len(gold_set - predicted)
            pool.append((case["id"], tickets))

        rows.append({"id": case["id"], "text": text, "ok": ok, "pred": describe(needs), "gold": case["gold"],
                     "rules_ms": round((t1 - t0) * 1000, 2), "llm": llm_info})
    return rows, totals, dd, failures, lat_rules, lat_llm, lat_total


def describe(needs):
    return [{"category": codebook.cat(n.category)["key"], "item_code": n.item_code, "priority": n.priority,
             "quantity": n.quantity, "zone": n.zone_code, "emergency": n.emergency, "flags": list(n.flags)}
            for n in needs]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--llm", choices=["none", "mock", "ollama"], default="none")
    ap.add_argument("--cases", default=str(ROOT / "eval" / "text_cases.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    ap.add_argument("--config", default=None, help="config file for --llm ollama")
    args = ap.parse_args(argv)

    cases = load_cases(args.cases)
    llm = make_llm(args.llm, args.config)
    rows, T, dd, failures, lat_r, lat_l, lat_t = evaluate(cases, llm)

    summary = {
        "llm": args.llm, "cases": len(cases), "needs_gold": T["gold"], "needs_pred": T["pred"],
        "need_precision": pct(T["matched"], T["pred"]), "need_recall": pct(T["matched"], T["gold"]),
        "category_recall": pct(T["cat_hit"], T["gold"]),
        "item_accuracy": pct(T["item_ok"], T["matched"]), "quantity_accuracy": pct(T["qty_ok"], T["matched"]),
        "zone_accuracy": pct(T["zone_ok"], T["matched"]), "priority_accuracy": pct(T["prio_ok"], T["matched"]),
        "emergency_accuracy": pct(T["emerg_ok"], len(cases)),
        "p1_recall": pct(T["p1_recalled"], T["gold_p1"]), "p1_precision": pct(T["p1_precise"], T["pred_p1"]),
        "gold_p1": T["gold_p1"], "pred_p1": T["pred_p1"],
        "dedupe_precision": pct(dd["tp"], dd["tp"] + dd["fp"]), "dedupe_recall": pct(dd["tp"], dd["tp"] + dd["fn"]),
        "dedupe_counts": dd,
        "latency_ms": {"rules_p50": percentile(lat_r, 50), "rules_p95": percentile(lat_r, 95),
                       "llm_p50": percentile(lat_l, 50), "llm_p95": percentile(lat_l, 95),
                       "total_p50": percentile(lat_t, 50), "total_p95": percentile(lat_t, 95)},
        "llm_errors": T["llm_errors"],
        "llm_fields": T["llm_fields"], "ungrounded_fields": T["ungrounded"],
        "hallucination_rate": pct(T["ungrounded"], T["llm_fields"]),
        "must_case_failures": failures,
    }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = "text_eval" if args.llm == "none" else f"text_eval_{args.llm}"
    (out / f"{stem}.json").write_text(json.dumps({"summary": summary, "cases": rows}, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    md = [f"# Text evaluation (--llm {args.llm})", "",
          f"Generated {time.strftime('%Y-%m-%d %H:%M')} on {len(cases)} labelled cases ({args.cases}).", "",
          "| Metric | Value |", "|---|---|"]
    for k in ("need_precision", "need_recall", "category_recall", "item_accuracy", "quantity_accuracy",
              "zone_accuracy", "priority_accuracy", "emergency_accuracy", "p1_recall", "p1_precision",
              "dedupe_precision", "dedupe_recall", "hallucination_rate"):
        v = summary[k]
        md.append(f"| {k} | {'n/a' if v is None else str(v) + ' %'} |")
    lm = summary["latency_ms"]
    md.append(f"| latency rules p50 / p95 | {lm['rules_p50']} / {lm['rules_p95']} ms |")
    if args.llm != "none":
        md.append(f"| latency LLM p50 / p95 | {lm['llm_p50']} / {lm['llm_p95']} ms |")
        md.append(f"| latency end-to-end p50 / p95 | {lm['total_p50']} / {lm['total_p95']} ms |")
        md.append(f"| LLM errors | {summary['llm_errors']} |")
    md += ["", f"Gold P1 needs: {T['gold_p1']}, predicted P1 needs: {T['pred_p1']}. "
           f"Dedupe pool: {dd}.", ""]
    if failures:
        md.append("## Must-be / must-not-be P1 failures")
        for f in failures:
            md.append(f"- `{f['id']}` — {f['why']}: {f['got']}")
    else:
        md.append("All must-be-P1 and must-not-be-P1 cases pass.")
    md += ["", "## Cases", "", "| id | ok | predicted (category/item/P) |", "|---|---|---|"]
    for r in rows:
        pred = ", ".join(f"{p['category']}/{p['item_code']}/P{p['priority']}" for p in r["pred"])
        md.append(f"| {r['id']} | {'✓' if r['ok'] else '✗'} | {pred} |")
    (out / f"{stem}.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"cases={len(cases)} llm={args.llm}")
    print(f"P1 recall={summary['p1_recall']}%  P1 precision={summary['p1_precision']}%  "
          f"(gold P1 {T['gold_p1']}, predicted P1 {T['pred_p1']})")
    print(f"need precision={summary['need_precision']}%  need recall={summary['need_recall']}%  "
          f"category recall={summary['category_recall']}%")
    print(f"field accuracy: item={summary['item_accuracy']}% quantity={summary['quantity_accuracy']}% "
          f"zone={summary['zone_accuracy']}% priority={summary['priority_accuracy']}%  "
          f"emergency={summary['emergency_accuracy']}%")
    print(f"dedupe precision={summary['dedupe_precision']}% recall={summary['dedupe_recall']}% {dd}")
    print(f"latency ms: {lm}")
    if args.llm != "none":
        print(f"LLM errors={summary['llm_errors']}  hallucination (ungrounded LLM fields)="
              f"{summary['hallucination_rate']}% of {T['llm_fields']}")
    print(f"must-case failures: {len(failures)}")
    for f in failures:
        print(f"  FAIL {f['id']}: {f['why']} -> {f['got']}")
    print(f"wrote {out / (stem + '.md')} and .json")
    if args.llm == "none" and (summary["p1_recall"] != 100.0 or failures):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
