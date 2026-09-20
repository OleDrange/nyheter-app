#!/usr/bin/env python3
"""Valider og importer nye quizspørsmål i quiz_bank/.

    python3 quiz_import.py kandidater/<slug>.json [...]        # valider + importer
    python3 quiz_import.py --check kandidater/<slug>.json [...] # kun valider
    python3 quiz_import.py --lint                               # lint hele banken
    python3 quiz_import.py --list <slug>                        # alt som ligger i én kategori

Hver kandidatfil heter <slug>.json eller <slug>.2.json (samme slug som i quiz_bank/) og inneholder en liste av
spørsmål: { difficulty, question, answer, options, explanation }. Filen importeres bare når
den har NULL feil — én feil holder hele filen tilbake, så retting skjer i kandidatfilen og
ikke i banken. Reglene her er den mekaniske delen av kriteriene i SKILL.md; skjønnet
(«lærer leseren noe om hvordan verden henger sammen?») ligger hos den som skriver."""
import json
import os
import re
import sys

BANK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "quiz_bank")
BANK = os.path.normpath(BANK)
DIFFS = ("easy", "medium", "hard")
MIN_EXPLANATION = 60
NEAR_JACCARD = 0.5
MAX_LONGEST_SHARE = 0.4

# Spørsmålsformer som er oppslag, ikke forståelse. Årstall er lov i spørsmålsteksten
# (som kontekst), men ikke som det som spørres om.
WEAK_QUESTION = re.compile(
    r"^(i )?hvilket år|hvilket årstall|når (ble|var|startet|begynte) .* \(år\)"
    r"|paragraf|§|hva står .{1,20} for|hva er forkortelsen"
    r"|^hva er hovedstaden i (norge|sverige|danmark)\b",
    re.I,
)
YEAR_ANSWER = re.compile(r"^(rundt |ca\.? |omkring )?(\d{1,4} (f|e)\.kr\.?|1\d{3}|20\d{2})$", re.I)
# Svar som endrer seg: sittende personer og «nyeste».
UNSTABLE = re.compile(r"\b(nåværende|sittende|nyeste|siste versjon|i dag er|per i dag)\b", re.I)
BAD_OPTION = re.compile(r"^(alle|ingen) (de )?(over|ovenfor|nevnte)", re.I)


def norm(t: str) -> str:
    t = re.sub(r"[^\w\s]", " ", (t or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def toks(t: str) -> set:
    return set(re.findall(r"\w{4,}", (t or "").lower()))


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def load_bank() -> dict:
    banks = {}
    for f in sorted(os.listdir(BANK)):
        if f.endswith(".json"):
            with open(os.path.join(BANK, f), encoding="utf-8") as fh:
                banks[f[:-5]] = json.load(fh)
    return banks


def check_question(q: dict, i: int, slug: str, bank_index: list, batch_index: list) -> list:
    """Returnerer liste av feilmeldinger for ett spørsmål. bank_index/batch_index:
    [(slug, norm_question, toks, norm_answer)] — batch_index er tidligere i samme runde."""
    errs = []
    where = f"{slug}[{i}]"
    if not isinstance(q, dict):
        return [f"{where}: ikke et objekt"]
    for k in ("difficulty", "question", "answer", "options", "explanation"):
        if not q.get(k):
            errs.append(f"{where}: mangler «{k}»")
    if errs:
        return errs
    qtext, ans, opts, expl = q["question"].strip(), q["answer"].strip(), q["options"], q["explanation"].strip()
    if q["difficulty"] not in DIFFS:
        errs.append(f"{where}: difficulty må være {DIFFS}")
    if not isinstance(opts, list) or len(opts) != 4:
        errs.append(f"{where}: options må være nøyaktig 4 (er {len(opts) if isinstance(opts, list) else '?'})")
    else:
        if len({norm(o) for o in opts}) != 4:
            errs.append(f"{where}: options har duplikater")
        if ans not in opts:
            errs.append(f"{where}: answer «{ans}» står ikke ordrett i options")
        for o in opts:
            if BAD_OPTION.match(o.strip()):
                errs.append(f"{where}: «{o}» — ingen «alle/ingen av de over»")
        lens = [len(o) for o in opts]
        if len(ans) > 1.8 * max(len(o) for o in opts if o != ans):
            errs.append(f"{where}: riktig svar er mye lengre enn distraktorene (gir det bort)")
    if WEAK_QUESTION.search(qtext):
        errs.append(f"{where}: oppslagsspørsmål (årstall/paragraf/forkortelse): «{qtext}»")
    if YEAR_ANSWER.match(ans):
        errs.append(f"{where}: svaret er et rent årstall — spør om rekkefølge/varighet/konsekvens i stedet")
    if UNSTABLE.search(qtext) or UNSTABLE.search(ans):
        errs.append(f"{where}: svaret ser ut til å kunne bli utdatert: «{qtext}»")
    if not qtext.endswith("?"):
        errs.append(f"{where}: spørsmålet slutter ikke med «?»")
    if len(expl) < MIN_EXPLANATION:
        errs.append(f"{where}: explanation for kort ({len(expl)} < {MIN_EXPLANATION} tegn) — den skal gi noe NYTT")
    if norm(expl) == norm(ans) or norm(expl).startswith(norm(ans) + " er riktig"):
        errs.append(f"{where}: explanation gjentar bare svaret")
    # Dedup mot banken og mot resten av runden.
    qn, qt, an = norm(qtext), toks(qtext), norm(ans)
    for src, index in (("banken", bank_index), ("runden", batch_index)):
        for oslug, on, ot, oa in index:
            if on == qn:
                errs.append(f"{where}: finnes allerede i {src} ({oslug}): «{qtext}»")
                break
            j = jaccard(qt, ot)
            if j > NEAR_JACCARD and (an == oa or j > 0.7):
                errs.append(f"{where}: nær-duplikat i {src} ({oslug}, {j:.2f}): «{qtext}»")
                break
    return errs


def lint_bank(banks: dict) -> int:
    """Kjør reglene på det som allerede ligger i banken. Manglende explanation i de gamle
    bankene rapporteres ikke (kjent, akseptert), resten rapporteres som advarsler."""
    n = 0
    index = []
    for slug, d in banks.items():
        for q in d.get("questions", []):
            index.append((slug, norm(q.get("question")), toks(q.get("question")), norm(q.get("answer"))))
    seen_pairs = set()
    for slug, d in banks.items():
        for i, q in enumerate(d.get("questions", [])):
            qq = dict(q)
            qq.setdefault("explanation", "x" * MIN_EXPLANATION)
            own = (slug, norm(q.get("question")), toks(q.get("question")), norm(q.get("answer")))
            others = [e for e in index if e is not own and not (e[0] == slug and e[1] == own[1])]
            for e in check_question(qq, i, slug, others, []):
                key = tuple(sorted([own[1], e.split("«")[-1]]))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                print("⚠ ", e)
                n += 1
    return n


def main() -> None:
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    banks = load_bank()
    if args == ["--lint"]:
        n = lint_bank(banks)
        print(f"\n{n} advarsler i banken")
        return
    if len(args) == 2 and args[0] == "--list":
        for q in banks.get(args[1], {}).get("questions", []):
            print(f"[{q.get('difficulty', '?')[0]}] {q.get('question')} → {q.get('answer')}")
        return
    check_only = args[0] == "--check"
    files = args[1:] if check_only else args
    bank_index = [
        (slug, norm(q.get("question")), toks(q.get("question")), norm(q.get("answer")))
        for slug, d in banks.items() for q in d.get("questions", [])
    ]
    batch_index: list = []
    total_err = 0
    ready: dict = {}
    for path in files:
        slug = re.sub(r"\.\d+$", "", os.path.basename(path)[:-5])  # <slug>.2.json → <slug>
        if slug not in banks:
            print(f"✗ {path}: ukjent kategori «{slug}» — opprett quiz_bank/{slug}.json først")
            total_err += 1
            continue
        try:
            with open(path, encoding="utf-8") as f:
                qs = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"✗ {path}: {exc}")
            total_err += 1
            continue
        if not isinstance(qs, list):
            print(f"✗ {path}: skal være en liste av spørsmål")
            total_err += 1
            continue
        errs = []
        for i, q in enumerate(qs):
            e = check_question(q, i, slug, bank_index, batch_index)
            errs.extend(e)
            if not e:
                batch_index.append((slug, norm(q["question"]), toks(q["question"]), norm(q["answer"])))
        # Lengden lekker: er riktig svar det lengste i de fleste spørsmålene, lærer leseren
        # å velge det lengste. Distraktorene skal like ofte være lengre.
        longest = [q for q in qs if isinstance(q, dict) and isinstance(q.get("options"), list) and q.get("answer")
                   and len(q["answer"]) >= max(len(o) for o in q["options"])]
        if qs and len(longest) / len(qs) > MAX_LONGEST_SHARE:
            errs.append(f"{slug}: riktig svar er lengst i {len(longest)}/{len(qs)} spørsmål (maks {MAX_LONGEST_SHARE:.0%}) — "
                        "gjør distraktorene lengre i noen: " + "; ".join(q["question"][:40] for q in longest[:5]))
        by_diff = {d: sum(1 for q in qs if isinstance(q, dict) and q.get("difficulty") == d) for d in DIFFS}
        for e in errs:
            print("✗ ", e)
        total_err += len(errs)
        print(f"{'✓' if not errs else '✗'} {slug}: {len(qs)} spørsmål {by_diff}, {len(errs)} feil")
        if not errs:
            ready.setdefault(slug, []).extend(qs)
    if check_only:
        print(f"\n{total_err} feil totalt")
        return
    if total_err:
        print(f"\n{total_err} feil — ingenting importert. Rett kandidatfilene og kjør igjen.")
        sys.exit(1)
    for slug, qs in ready.items():
        d = banks[slug]
        d["questions"].extend(
            {"difficulty": q["difficulty"], "question": q["question"].strip(), "answer": q["answer"].strip(),
             "options": [o.strip() for o in q["options"]], "explanation": q["explanation"].strip()}
            for q in qs
        )
        with open(os.path.join(BANK, slug + ".json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"→ {slug}: +{len(qs)} → {len(d['questions'])} i banken")


if __name__ == "__main__":
    main()
