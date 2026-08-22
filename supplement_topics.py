#!/usr/bin/env python3
"""
supplement_topics.py  —  Emneregisteret for tilskudds-kunnskapsbasen

Ett register, én sannhet — samme rolle som `textile_topics.py` har for tekstil.
Generatoren bruker `TOPICS` til å (1) tildele studier til stoffer lokalt og gratis,
(2) bygge `supplement_kb.json`, som nettsiden leser, og (3) måle bevis-gulvet for
stoffer som ikke har en eneste kvalifiserende studie. Nettsiden har INGEN egen
stoffliste — legger du til et stoff her, dukker det opp på forskning.modr.no/tilskudd
ved neste generatorkjøring, uten kodeendring i `web/`.

Feltene per stoff:
    slug     — URL-en (/tilskudd/<slug>) og nøkkelen i KB-en. Endres ALDRI etter
               publisering: den er identiteten til all evidens som er rullet inn.
    name     — visningsnavn (norsk)
    kind     — basis | ytelse | longevity | sovn | tarm | uavklart. Styrer gruppering.
    blurb    — én setning om hva stoffet er, vist før Claude har syntetisert noe.
    claim    — HVA DET MARKEDSFØRES SOM. Dette feltet er hele poenget med oppslagsverket:
               syntesen får det med i prompten og blir bedt om å sette det opp mot hva
               studiene FAKTISK har målt. For NAD-forløpere er påstanden «klarhet i
               hodet», mens utfallet i studiene er «NAD+ i helblod steg 60 %» — å se de
               to setningene ved siden av hverandre er den mest verdifulle enkeltlinjen
               på hele siden.
    terms    — søkeord (små bokstaver) som knytter en studie til stoffet. Treff i
               TITTELEN gjør stoffet primært, treff kun i sammendraget sekundært
               (se `assign_topics` i supplement_briefing.py). Engelsk — kildene er det.
    probe    — Europe PMC-tittelspørring. Brukes både til å HENTE studier om stoffet og
               til å måle bevis-gulvet (antall rå treff mot antall som passerer
               kvalitetsfiltrene). Registeret er dermed også søkespesifikasjonen: et nytt
               stoff her blir automatisk søkt opp, uten kodeendring i generatoren.
    floor_verdict (valgfritt)
             — dommen stoffet skal ha så lenge det IKKE finnes kvalifiserende evidens.
               Den utledes ikke av studier — den utledes av regulatorisk status og av at
               stoffet injiseres uten sikkerhetsdata. Claude får aldri sette den; den står
               her fordi den er et faktum om markedet, ikke en tolkning av forskning.
               Uten feltet faller et tomt stoff til `ukjent`, som er riktig for et
               harmløst, dårlig studert stoff (glysin) og feil for et gråmarkedspeptid.

Antall kvalifiserende studier per stoff er MÅLT mot Europe PMC 22. august 2026
(730-dagers vindu, KW:"Humans" + RCT/metaanalyse/systematisk oversikt + SRC:MED).
Tallet står i kommentar over hvert stoff der det er interessant — særlig der det er 0.
"""

# ─────────────────────────────────────────────────────────────────────────────
# BASIS — vitaminer og mineraler. Der spørsmålet nesten alltid er «har jeg mangel?»
# og ikke «virker stoffet?».
# ─────────────────────────────────────────────────────────────────────────────
_BASIS = [
    dict(
        slug="d-vitamin", name="D-vitamin", kind="basis",
        blurb="Fettløselig vitamin som huden lager av sollys. På Vestlandet er "
              "vinterhalvåret det som gjør spørsmålet reelt.",
        claim="Sterkere skjelett, bedre immunforsvar, mindre depresjon og lavere "
              "dødelighet — nesten alt har vært tilskrevet D-vitamin.",
        terms=["vitamin d", "cholecalciferol", "25-hydroxyvitamin d", "ergocalciferol",
               "vitamin d3"],
        probe='(TITLE:"vitamin D" OR TITLE:"cholecalciferol")',
    ),
    dict(
        slug="omega-3", name="Omega-3 (EPA/DHA)", kind="basis",
        blurb="Langkjedede marine fettsyrer. Det mest studerte enkelttilskuddet på "
              "hjerte- og karsykdom.",
        claim="Beskytter hjertet, demper betennelse, bedrer humør og hjernefunksjon.",
        terms=["omega-3", "omega 3", "fish oil", "epa and dha", "docosahexaenoic",
               "eicosapentaenoic", "n-3 fatty acid", "n-3 polyunsaturated"],
        probe='(TITLE:"omega-3" OR TITLE:"fish oil" OR TITLE:"n-3 fatty acid" '
              'OR TITLE:"docosahexaenoic" OR TITLE:"eicosapentaenoic")',
    ),
    dict(
        slug="magnesium", name="Magnesium", kind="basis",
        blurb="Mineral involvert i flere hundre enzymreaksjoner. Formen (sitrat, oksid, "
              "glysinat, treonat) avgjør hvor mye som faktisk tas opp.",
        claim="Bedre søvn, mindre kramper, lavere blodtrykk, mindre angst.",
        terms=["magnesium"],
        probe='(TITLE:"magnesium")',
    ),
    dict(
        slug="sink", name="Sink", kind="basis",
        blurb="Sporstoff med rolle i immunforsvar og sårheling. Smalt vindu mellom for "
              "lite og for mye — høye doser hemmer kobberopptaket.",
        claim="Kortere forkjølelse, bedre immunforsvar, høyere testosteron.",
        terms=["zinc"],
        probe='(TITLE:"zinc")',
    ),
    dict(
        slug="jern", name="Jern", kind="basis",
        blurb="Det eneste tilskuddet på denne listen der overdosering er direkte "
              "farlig for en frisk voksen mann. Skal ikke tas uten målt mangel.",
        claim="Mer energi, mindre tretthet.",
        terms=["iron supplementation", "iron deficiency", "ferritin", "ferrous sulfate",
               "iron status"],
        probe='(TITLE:"iron supplementation" OR TITLE:"iron deficiency")',
    ),
    dict(
        slug="b12-og-folat", name="B12 og folat", kind="basis",
        blurb="To B-vitaminer som henger sammen i samme metabolske syklus. B12 er den "
              "reelle risikoen ved plantebasert kosthold.",
        claim="Mer energi, bedre hukommelse, lavere homocystein og dermed lavere "
              "hjerterisiko.",
        terms=["vitamin b12", "cobalamin", "folate", "folic acid", "homocysteine",
               "b-vitamin"],
        probe='(TITLE:"vitamin B12" OR TITLE:"cobalamin" OR TITLE:"folate" '
              'OR TITLE:"folic acid")',
    ),
    dict(
        slug="c-vitamin", name="C-vitamin", kind="basis",
        blurb="Vannløselig antioksidant. Kroppen metter seg ved beskjedne doser — "
              "resten går ut med urinen.",
        claim="Færre og kortere forkjølelser, bedre immunforsvar, mindre oksidativt "
              "stress.",
        terms=["vitamin c", "ascorbic acid", "ascorbate"],
        probe='(TITLE:"vitamin C" OR TITLE:"ascorbic acid")',
    ),
    # 9 kvalifiserende studier på to år — tynt, og det skal oppslaget si.
    dict(
        slug="k2-vitamin", name="K2-vitamin", kind="basis",
        blurb="Fettløselig vitamin som aktiverer proteiner i skjelett og blodårer. "
              "Selges nesten alltid sammen med D-vitamin.",
        claim="Dirigerer kalsium til skjelettet i stedet for til blodårene, og "
              "forebygger dermed både benskjørhet og åreforkalkning.",
        terms=["vitamin k2", "menaquinone", "mk-7", "vitamin k supplementation"],
        probe='(TITLE:"vitamin K2" OR TITLE:"menaquinone")',
    ),
    dict(
        slug="multivitamin", name="Multivitamin", kind="basis",
        blurb="Alt-i-ett-forsikringen. Det mest solgte tilskuddet, og det mest studerte "
              "på harde utfall som død og hjertesykdom.",
        claim="Dekker hullene i kostholdet og gir en generell helsegevinst.",
        terms=["multivitamin", "multimineral", "multinutrient supplement"],
        probe='(TITLE:"multivitamin" OR TITLE:"multinutrient")',
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# YTELSE — det som faktisk skal måles i kilo, watt og sekunder
# ─────────────────────────────────────────────────────────────────────────────
_YTELSE = [
    dict(
        slug="kreatin", name="Kreatin", kind="ytelse",
        blurb="Monohydratformen er det best dokumenterte prestasjonstilskuddet som "
              "finnes, og et av de billigste.",
        claim="Mer styrke og muskelmasse — og i nyere markedsføring også bedre "
              "kognisjon og humør.",
        terms=["creatine", "creatine monohydrate", "phosphocreatine supplementation"],
        probe='(TITLE:"creatine")',
    ),
    dict(
        slug="proteintilskudd", name="Proteintilskudd", kind="ytelse",
        blurb="Myse, kasein eller plantebasert. Spørsmålet er sjelden om protein "
              "virker, men om pulveret gir noe utover maten du allerede spiser.",
        claim="Mer muskelvekst og raskere restitusjon etter trening.",
        terms=["whey protein", "protein supplementation", "casein", "soy protein",
               "protein supplement", "essential amino acid supplement"],
        probe='(TITLE:"whey protein" OR TITLE:"protein supplementation")',
    ),
    dict(
        slug="koffein", name="Koffein", kind="ytelse",
        blurb="Verdens mest brukte psykoaktive stoff, og et prestasjonstilskudd med "
              "målbar effekt. Toleranse og søvnforstyrrelse er baksiden.",
        claim="Mer utholdenhet, høyere våkenhet, bedre fokus.",
        terms=["caffeine"],
        probe='(TITLE:"caffeine")',
    ),
    dict(
        slug="nitrater", name="Nitrater og rødbete", kind="ytelse",
        blurb="Kostnitrat omdannes til nitrogenoksid, som utvider blodårene. "
              "Rødbetjuice er den vanlige kilden i studiene.",
        claim="Bedre utholdenhet, lavere blodtrykk, mer oksygen til muskelen.",
        terms=["beetroot", "dietary nitrate", "nitrate supplementation", "nitric oxide",
               "beet juice"],
        probe='(TITLE:"beetroot" OR TITLE:"dietary nitrate")',
    ),
    dict(
        slug="beta-alanin", name="Beta-alanin", kind="ytelse",
        blurb="Bygger opp karnosin i muskelen og bufrer syre. Virker i et smalt "
              "arbeidsvindu — 1 til 4 minutters maksimalt arbeid.",
        claim="Utsetter muskeltretthet ved hard, kortvarig innsats.",
        terms=["beta-alanine", "beta alanine", "carnosine supplementation"],
        probe='(TITLE:"beta-alanine")',
    ),
    dict(
        slug="hmb", name="HMB", kind="ytelse",
        blurb="Nedbrytningsprodukt av leucin. Markedsført hardt mot muskeltap hos "
              "eldre, med et evidensgrunnlag som spriker kraftig.",
        claim="Bremser muskelnedbrytning og bevarer muskelmasse ved inaktivitet og "
              "alder.",
        terms=["hmb", "beta-hydroxy-beta-methylbutyrate", "hydroxymethylbutyrate"],
        probe='(TITLE:"HMB" OR TITLE:"beta-hydroxy-beta-methylbutyrate")',
    ),
    dict(
        slug="kollagen", name="Kollagen", kind="ytelse",
        blurb="Hydrolysert bindevevsprotein. Fordøyes til vanlige aminosyrer — "
              "spørsmålet er om noe likevel havner der det skal.",
        claim="Sterkere sener og ledd, mindre smerte, fastere hud.",
        terms=["collagen peptide", "collagen supplementation", "hydrolyzed collagen",
               "collagen hydrolysate"],
        probe='(TITLE:"collagen peptide" OR TITLE:"collagen supplementation")',
    ),
    dict(
        slug="elektrolytter", name="Elektrolytter og bikarbonat", kind="ytelse",
        blurb="Salter til væskebalanse, og natriumbikarbonat som syrebuffer ved hard "
              "innsats. To ganske ulike bruksområder i samme kategori.",
        claim="Bedre væskebalanse, færre kramper, høyere kapasitet i harde intervaller.",
        terms=["electrolyte supplementation", "sodium bicarbonate", "rehydration",
               "electrolyte drink", "sodium citrate supplementation"],
        probe='(TITLE:"electrolyte supplementation" OR TITLE:"sodium bicarbonate")',
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# LONGEVITY — der påstandene er størst og utfallene som måles er minst
# ─────────────────────────────────────────────────────────────────────────────
_LONGEVITY = [
    # 23 kvalifiserende studier. Nok til et reelt oppslag — og nok til å vise gapet
    # mellom påstanden («klarhet i hodet») og utfallet studiene faktisk måler
    # (NAD+-konsentrasjon i helblod).
    dict(
        slug="nad-forlopere", name="NAD-forløpere (NR og NMN)", kind="longevity",
        blurb="Nikotinamidribosid og nikotinamidmononukleotid — forstadier til NAD+, "
              "et koenzym som faller med alderen.",
        claim="Snur biologisk aldring, gir mer energi og klarhet i hodet.",
        terms=["nicotinamide riboside", "nicotinamide mononucleotide", "nad+",
               "nad precursor", "nicotinamide adenine dinucleotide supplementation"],
        probe='(TITLE:"nicotinamide riboside" OR TITLE:"nicotinamide mononucleotide" '
              'OR TITLE:"NAD+")',
    ),
    dict(
        slug="resveratrol", name="Resveratrol", kind="longevity",
        blurb="Polyfenol fra drueskall. Den opprinnelige «rødvinsmolekylet»-historien, "
              "med et biotilgjengelighetsproblem som aldri ble løst.",
        claim="Aktiverer sirtuiner og etterligner kalorirestriksjon — forlenger livet.",
        terms=["resveratrol", "pterostilbene"],
        probe='(TITLE:"resveratrol")',
    ),
    dict(
        slug="spermidin", name="Spermidin", kind="longevity",
        blurb="Polyamin som finnes i hvetekim og gammel ost. Utløser autofagi i "
              "cellemodeller.",
        claim="Setter i gang cellenes selvrensing (autofagi) og forlenger livet.",
        terms=["spermidine", "polyamine supplementation", "autophagy inducer"],
        probe='(TITLE:"spermidine")',
    ),
    dict(
        slug="q10", name="Koenzym Q10", kind="longevity",
        blurb="Del av mitokondrienes energikjede. Den ene godt dokumenterte "
              "indikasjonen er å motvirke muskelplager av statiner.",
        claim="Mer cellulær energi, bedre hjertefunksjon, mindre tretthet.",
        terms=["coenzyme q10", "ubiquinol", "ubiquinone", "coq10"],
        probe='(TITLE:"coenzyme Q10" OR TITLE:"ubiquinol")',
    ),
    dict(
        slug="kurkumin", name="Kurkumin", kind="longevity",
        blurb="Gurkemeiens aktive stoff. Nesten uten opptak alene — studiene bruker "
              "formuleringer med piperin eller fosfolipid.",
        claim="Demper kronisk betennelse, beskytter ledd og hjerne.",
        terms=["curcumin", "turmeric", "curcuma longa"],
        probe='(TITLE:"curcumin" OR TITLE:"turmeric")',
    ),
    dict(
        slug="taurin", name="Taurin", kind="longevity",
        blurb="Aminosyre som fikk oppmerksomhet etter en mye omtalt dyrestudie i 2023. "
              "Humanstudiene er langt tynnere.",
        claim="Bremser aldring og bedrer metabolsk helse.",
        terms=["taurine"],
        probe='(TITLE:"taurine")',
    ),
    dict(
        slug="berberin", name="Berberin", kind="longevity",
        blurb="Plantealkaloid markedsført som «naturens Ozempic». Har reell "
              "blodsukkersenkende effekt, men også legemiddelinteraksjoner.",
        claim="Senker blodsukker og kolesterol som et legemiddel, uten resept.",
        terms=["berberine"],
        probe='(TITLE:"berberine")',
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# SØVN, STRESS OG HUMØR
# ─────────────────────────────────────────────────────────────────────────────
_SOVN = [
    dict(
        slug="melatonin", name="Melatonin", kind="sovn",
        blurb="Kroppens eget mørkehormon. Dosen i studiene er gjennomgående mye lavere "
              "enn dosen som selges.",
        claim="Sovner raskere, sover bedre, kurerer jetlag.",
        terms=["melatonin"],
        probe='(TITLE:"melatonin")',
    ),
    dict(
        slug="ashwagandha", name="Ashwagandha", kind="sovn",
        blurb="Ayurvedisk adaptogen. Den mest lovende av urtetilskuddene mot stress — "
              "og den med flest små, industrifinansierte studier.",
        claim="Senker kortisol og stress, bedrer søvn og testosteron.",
        terms=["ashwagandha", "withania somnifera", "withanolide"],
        probe='(TITLE:"ashwagandha" OR TITLE:"Withania")',
    ),
    dict(
        slug="l-teanin", name="L-teanin", kind="sovn",
        blurb="Aminosyre fra grønn te. Brukes mot koffeinets nervøsitet uten å ta bort "
              "våkenheten.",
        claim="Rolig fokus, mindre stress, bedre søvnkvalitet.",
        terms=["l-theanine", "theanine"],
        probe='(TITLE:"L-theanine" OR TITLE:"theanine")',
    ),
    dict(
        slug="rhodiola", name="Rhodiola", kind="sovn",
        blurb="Rosenrot. Adaptogen med tradisjon fra nordlige strøk, og et lite antall "
              "moderne forsøk.",
        claim="Mindre utmattelse, bedre mental utholdenhet under press.",
        terms=["rhodiola", "rosea supplementation", "salidroside"],
        probe='(TITLE:"Rhodiola")',
    ),
    # 0 kvalifiserende studier på to år. Bevis-gulvet er selve oppslaget.
    dict(
        slug="glysin", name="Glysin", kind="sovn",
        blurb="Enkleste aminosyre, markedsført mot søvn. Nesten all evidens stammer "
              "fra et par små japanske forsøk fra 2007.",
        claim="Raskere innsovning og dypere søvn.",
        terms=["glycine supplementation", "glycine sleep"],
        probe='(TITLE:"glycine supplementation")',
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# TARM
# ─────────────────────────────────────────────────────────────────────────────
_TARM = [
    dict(
        slug="probiotika", name="Probiotika", kind="tarm",
        blurb="Levende bakteriekulturer. «Probiotika» er ikke ett produkt — effekten "
              "er stammespesifikk, og en studie på én stamme sier lite om en annen.",
        claim="Bedre fordøyelse, sterkere immunforsvar, bedre humør via tarm-hjerne-aksen.",
        terms=["probiotic", "synbiotic", "lactobacillus", "bifidobacterium",
               "gut microbiota supplementation"],
        probe='(TITLE:"probiotic" OR TITLE:"synbiotic")',
    ),
    dict(
        slug="prebiotika-fiber", name="Prebiotika og fibertilskudd", kind="tarm",
        blurb="Psyllium, inulin og resistent stivelse. Det eneste «tarmtilskuddet» der "
              "virkningsmekanismen er triviell og godt forstått.",
        claim="Bedre tarmflora, stabilt blodsukker, lavere kolesterol.",
        terms=["prebiotic", "psyllium", "inulin", "resistant starch",
               "dietary fiber supplementation", "beta-glucan supplementation"],
        probe='(TITLE:"prebiotic" OR TITLE:"psyllium" OR TITLE:"inulin" '
              'OR TITLE:"resistant starch")',
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# UAVKLART OG UREGULERT
#
# Dette er ikke en restkategori — det er den viktigste gruppen på hele siden.
# Stoffene her har 0 kvalifiserende humanstudier (målt 22. august 2026), men det
# finnes 193 publikasjoner om BPC-157 og 395 om de øvrige peptidene. Fraværet av
# kontrollerte forsøk ER svaret, og det er nettopp disse stoffene som markedsføres
# hardest. Et emne uten oppslag ville latt leseren tro at spørsmålet ikke er stilt.
# Se `evidence_floor` i supplement_briefing.py.
# ─────────────────────────────────────────────────────────────────────────────
_UAVKLART = [
    dict(
        slug="bpc-157", name="BPC-157", kind="uavklart",
        blurb="Syntetisk peptidfragment fra magesaft. Selges som «forskningskjemikalie» "
              "og injiseres av idrettsutøvere. Ikke godkjent som legemiddel noe sted.",
        floor_verdict="risiko",
        claim="Leger sener, ledd og tarm i rekordfart.",
        terms=["bpc 157", "bpc-157", "body protection compound"],
        probe='(TITLE:"BPC 157" OR TITLE:"BPC-157" OR TITLE:"body protection compound")',
    ),
    dict(
        slug="andre-peptider", name="TB-500 og vekstpeptider", kind="uavklart",
        blurb="Tymosin beta-4, ipamorelin, CJC-1295 og slektninger. Samme gråmarked "
              "som BPC-157, samme fravær av kontrollerte forsøk på mennesker.",
        floor_verdict="risiko",
        claim="Raskere restitusjon, mer vekst, bedre søvn — via kroppens eget "
              "veksthormonsystem.",
        terms=["tb-500", "tb 500", "thymosin beta", "ipamorelin", "cjc-1295",
               "sermorelin", "ghrp-6", "growth hormone secretagogue"],
        probe='(TITLE:"TB-500" OR TITLE:"thymosin beta" OR TITLE:"ipamorelin" '
              'OR TITLE:"CJC-1295" OR TITLE:"growth hormone secretagogue")',
    ),
    dict(
        slug="medisinsopp", name="Medisinsk sopp", kind="uavklart",
        blurb="Løvemanke, cordyceps og reishi. Stor tradisjonsbruk, mye "
              "laboratoriearbeid, nesten ingen kontrollerte forsøk på mennesker.",
        claim="Bedre kognisjon og nervevekst, mer utholdenhet, sterkere immunforsvar.",
        terms=["lion's mane", "hericium", "cordyceps", "reishi", "ganoderma",
               "medicinal mushroom"],
        probe="(TITLE:\"lion's mane\" OR TITLE:\"Hericium\" OR TITLE:\"Cordyceps\" "
              'OR TITLE:"Reishi" OR TITLE:"Ganoderma")',
    ),
]

TOPICS: list[dict] = _BASIS + _YTELSE + _LONGEVITY + _SOVN + _TARM + _UAVKLART

TOPICS_BY_SLUG: dict[str, dict] = {t["slug"]: t for t in TOPICS}

# Rekkefølgen gruppene vises i, og visningsnavn. Nettsiden leser dette fra KB-en.
KIND_LABELS: dict[str, str] = {
    "basis": "Basis — vitaminer og mineraler",
    "ytelse": "Ytelse og muskel",
    "longevity": "Longevity og metabolsk helse",
    "sovn": "Søvn, stress og humør",
    "tarm": "Tarm og fordøyelse",
    "uavklart": "Uavklart og uregulert",
}
KIND_ORDER: list[str] = ["basis", "ytelse", "longevity", "sovn", "tarm", "uavklart"]

# Dommen et stoff kan ende på. Egen akse, ikke tekstilens — se CLAUDE.md.
#
# `dropp` og `ukjent` er BEVISST to forskjellige dommer: «godt studert, effekten er
# null» og «vi vet ikke» er helt ulike svar, og et oppslagsverk som slår dem sammen
# er verdiløst. `risiko` er skilt fra begge fordi et uregulert peptid uten
# sikkerhetsdata ikke skal kunne leses som «kanskje verdt et forsøk».
VERDICTS: dict[str, str] = {
    "ta": "Verdt å ta",
    "vurder": "Kan vurderes",
    "dropp": "Dokumentert uten effekt",
    "risiko": "Frarådes",
    "ukjent": "For tynt grunnlag",
}
VERDICT_ORDER: list[str] = ["ta", "vurder", "dropp", "risiko", "ukjent"]

STRENGTHS: dict[str, str] = {
    "sterk": "Sterk evidens",
    "moderat": "Moderat evidens",
    "svak": "Svak evidens",
}
