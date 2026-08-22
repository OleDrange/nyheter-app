#!/usr/bin/env python3
"""
textile_topics.py  —  Emneregisteret for tekstil-kunnskapsbasen

Ett register, én sannhet. Generatoren bruker `TOPICS` til å (1) tildele studier til emner
lokalt og gratis, og (2) bygge opp `textile_kb.json`, som nettsiden leser. Nettsiden har
INGEN egen emneliste — legger du til et emne her, dukker det opp på tekstil.modr.no ved
neste generatorkjøring, uten kodeendring i `web/`.

Feltene per emne:
    slug     — URL-en (/emne/<slug>) og nøkkelen i KB-en. Endres ALDRI etter publisering:
               den er identiteten til all evidens som er rullet inn under emnet.
    name     — visningsnavn (norsk)
    kind     — fiber | behandling | kvalitet | miljo. Styrer gruppering på forsiden.
    blurb    — én setning som forklarer hva emnet er, vist før Claude har syntetisert noe.
    terms    — søkeord (små bokstaver) som knytter en studie til emnet. Treff i TITTELEN
               gjør emnet primært, treff kun i sammendraget gjør det sekundært
               (se `assign_topics` i textile_briefing.py). Skriv dem på engelsk —
               kildene er engelskspråklige.

Én studie kan treffe flere emner (en PFAS-impregnert bomullsjakke er både `pfas` og
`bomull`), og det er meningen: evidensen skal dukke opp der leseren leter.
"""

# ─────────────────────────────────────────────────────────────────────────────
# FIBRE — det første valget i en produktspesifikasjon
# ─────────────────────────────────────────────────────────────────────────────
_FIBERS = [
    dict(
        slug="bomull", name="Bomull", kind="fiber",
        blurb="Verdens mest brukte naturfiber. Pustende og hudvennlig i seg selv — "
              "spørsmålet er nesten alltid hva som er gjort MED den.",
        terms=["cotton", "gossypium"],
    ),
    dict(
        slug="okologisk-bomull", name="Økologisk bomull", kind="fiber",
        blurb="Bomull dyrket uten syntetiske plantevernmidler. Sier noe om dyrkingen, "
              "ikke nødvendigvis om kjemikaliene i etterbehandlingen.",
        terms=["organic cotton", "gots", "organic farming cotton"],
    ),
    dict(
        slug="lin", name="Lin", kind="fiber",
        blurb="Bastfiber fra lin. Sterkt vått, svært slitesterkt, lite behov for "
              "kjemisk etterbehandling — men krøller.",
        terms=["linen", "flax fibre", "flax fiber", "linum usitatissimum"],
    ),
    dict(
        slug="hamp", name="Hamp", kind="fiber",
        blurb="Bastfiber med høy strekkfasthet og lavt innsatsbehov i dyrking. "
              "Røyting og bleking er de kjemisk kritiske stegene.",
        terms=["hemp fibre", "hemp fiber", "cannabis sativa fibre", "hemp textile"],
    ),
    dict(
        slug="ull", name="Ull og merino", kind="fiber",
        blurb="Proteinfiber. Temperaturregulerende og luktresistent; ullkløe handler om "
              "fiberdiameter, ikke allergi. Superwash-behandlingen er den kjemiske haken.",
        terms=["wool", "merino", "keratin fibre", "cashmere", "alpaca"],
    ),
    dict(
        slug="silke", name="Silke", kind="fiber",
        blurb="Proteinfiber med lav friksjon mot hud. Ofte fremhevet ved eksem — "
              "evidensen er tynnere enn markedsføringen antyder.",
        terms=["silk fabric", "silk textile", "sericin", "fibroin", "bombyx mori"],
    ),
    dict(
        slug="viskose", name="Viskose og rayon", kind="fiber",
        blurb="Regenerert cellulose laget med karbondisulfid. Behagelig mot hud, "
              "men prosessen er den mest problematiske blant cellulosefibrene.",
        terms=["viscose", "rayon", "carbon disulfide", "carbon disulphide"],
    ),
    dict(
        slug="lyocell", name="Lyocell og modal", kind="fiber",
        blurb="Regenerert cellulose i lukket løsemiddelkretsløp (NMMO). Alternativet til "
              "viskose når prosesskjemien er problemet.",
        terms=["lyocell", "tencel", "modal fibre", "modal fiber", "nmmo"],
    ),
    dict(
        slug="bambusviskose", name="Bambusviskose", kind="fiber",
        blurb="Markedsføres som naturfiber, men er kjemisk regenerert cellulose — "
              "samme prosess som viskose.",
        terms=["bamboo fibre", "bamboo fiber", "bamboo textile", "bamboo viscose"],
    ),
    dict(
        slug="polyester", name="Polyester", kind="fiber",
        blurb="Billigst, sterkest, mest brukt. Bærer restmonomerer og dispersjonsfarger, "
              "og er hovedkilden til mikrofiberutslipp fra vask.",
        terms=["polyester", "polyethylene terephthalate", " pet fibre", " pet fiber"],
    ),
    dict(
        slug="resirkulert-polyester", name="Resirkulert polyester", kind="fiber",
        blurb="rPET fra flasker eller tekstil. Løser råvarespørsmålet, ikke "
              "mikrofiberutslippet — og kan bære med seg forurensninger fra kilden.",
        terms=["recycled polyester", "rpet", "recycled pet", "mechanically recycled polyester"],
    ),
    dict(
        slug="nylon", name="Nylon og polyamid", kind="fiber",
        blurb="Slitesterk syntet i sportstøy og strømper. Restmonomer kaprolaktam og "
              "impregnering er de aktuelle problemstillingene.",
        terms=["nylon", "polyamide fibre", "polyamide fiber", "caprolactam"],
    ),
    dict(
        slug="elastan", name="Elastan og spandex", kind="fiber",
        blurb="Gir stretch, men degraderes av svette, varme og vask — ofte det som "
              "avgjør hvor lenge et plagg holder formen.",
        terms=["elastane", "spandex", "lycra", "polyurethane fibre", "polyurethane fiber"],
    ),
    dict(
        slug="akryl", name="Akryl", kind="fiber",
        blurb="Ullimitasjon av polyakrylnitril. Piller lett, og restakrylnitril er "
              "et kjent spørsmål.",
        terms=["acrylic fibre", "acrylic fiber", "polyacrylonitrile", "acrylonitrile"],
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# BEHANDLINGER OG KJEMIKALIER — det som faktisk havner mot huden
# ─────────────────────────────────────────────────────────────────────────────
_TREATMENTS = [
    dict(
        slug="azo-og-dispersjonsfarger", name="Azo- og dispersjonsfarger", kind="behandling",
        blurb="Den klart hyppigste årsaken til tekstilallergi. Dispersjonsfargene "
              "(til polyester) sitter dårlig fast og vandrer ut i svette.",
        terms=["azo dye", "disperse dye", "disperse blue", "disperse orange", "disperse red",
               "textile dye allergy", "aromatic amine", "dye dermatitis"],
    ),
    dict(
        slug="reaktivfarging", name="Reaktivfarging", kind="behandling",
        blurb="Fargestoffet bindes kovalent til cellulosen. God fargeekthet og lite "
              "migrasjon — men høyt salt- og vannforbruk.",
        terms=["reactive dye", "reactive dyeing", "vat dye", "direct dye", "natural dye"],
    ),
    dict(
        slug="formaldehyd", name="Formaldehyd og krøllfrihet", kind="behandling",
        blurb="«Easy-care», «non-iron» og «wrinkle-free» betyr som regel harpiks som "
              "avgir formaldehyd. Klassisk kilde til kontakteksem.",
        terms=["formaldehyde", "wrinkle resistant finish", "easy care finish",
               "dmdheu", "durable press", "melamine resin"],
    ),
    dict(
        slug="pfas", name="PFAS og impregnering", kind="behandling",
        blurb="Vann- og smussavvisende behandling (DWR). Persistent, bioakkumulerende og "
              "under utfasing i EU — det tydeligste «utelukkes»-kandidatet.",
        terms=["pfas", "perfluoroalkyl", "polyfluoroalkyl", "pfoa", "pfos", "pfhxs",
               "fluorocarbon finish", "water repellent finish", "durable water repellent",
               "side-chain fluorinated"],
    ),
    dict(
        slug="flammehemmere", name="Flammehemmere", kind="behandling",
        blurb="Bromerte og fosfororganiske flammehemmere. Krav i barnenattøy og "
              "arbeidstøy, men flere er hormonforstyrrende.",
        terms=["flame retardant", "brominated flame retardant", "organophosphate ester",
               "decabde", "tris(", "tcpp", "tdcipp", "flame retardancy"],
    ),
    dict(
        slug="antibakteriell-behandling", name="Antibakteriell behandling", kind="behandling",
        blurb="Nanosølv, triklosan og kvartære ammoniumforbindelser mot lukt. "
              "Vaskes ut, påvirker hudmikrobiomet og driver resistens.",
        terms=["antimicrobial textile", "antibacterial fabric", "silver nanoparticle",
               "nanosilver", "triclosan", "quaternary ammonium", "zinc pyrithione",
               "antimicrobial finish", "odour control textile"],
    ),
    dict(
        slug="mykgjorere-og-ftalater", name="Mykgjørere og ftalater", kind="behandling",
        blurb="Softenere, plastisol-trykk og PVC-detaljer. Ftalater er hormonforstyrrende "
              "og finnes særlig i trykk på barneklær.",
        terms=["phthalate", "dehp", "plasticizer", "plasticiser", "fabric softener",
               "plastisol", "pvc coating", "bisphenol a textile"],
    ),
    dict(
        slug="tungmetaller", name="Tungmetaller og metalldeler", kind="behandling",
        blurb="Nikkel i knapper og glidelåser, krom i skinn, antimon fra polyester. "
              "Nikkelallergi er den vanligste kontaktallergien i Europa.",
        terms=["nickel release", "nickel allergy", "chromium vi", "hexavalent chromium",
               "chromium allergy", "antimony", "cadmium textile", "lead textile",
               "heavy metal textile"],
    ),
    dict(
        slug="superwash-ull", name="Superwash-ull", kind="behandling",
        blurb="Klor-Hercosett-behandling som fjerner ullas krympeevne ved å kle "
              "fiberen i polymer. Gir maskinvaskbar ull — og AOX-utslipp.",
        terms=["superwash", "chlorine hercosett", "shrink resist wool", "wool anti-felting"],
    ),
    dict(
        slug="bleking-og-optisk-hvitt", name="Bleking og optisk hvitt", kind="behandling",
        blurb="Klor- eller peroksidbleking pluss optiske hvitemidler (fluorescerende). "
              "Svekker fiberen og gir en kjent, om enn sjelden, allergikilde.",
        terms=["optical brightener", "fluorescent whitening", "bleaching cotton",
               "hydrogen peroxide bleaching", "chlorine bleaching textile", "scouring"],
    ),
    dict(
        slug="garving", name="Garving av skinn", kind="behandling",
        blurb="Kromgarving dominerer og gir kromallergi-risiko og tungt avløpsvann; "
              "vegetabilsk garving er alternativet.",
        terms=["leather tanning", "chrome tanning", "vegetable tanning", "tannery",
               "leather chromium"],
    ),
    dict(
        slug="nonylfenol-og-tensider", name="Nonylfenol og tensider", kind="behandling",
        blurb="Vaske- og dispergeringsmidler i produksjonen. NPE brytes ned til "
              "hormonforstyrrende nonylfenol og er REACH-begrenset.",
        terms=["nonylphenol", "alkylphenol ethoxylate", "surfactant textile",
               "detergent residue textile"],
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# KVALITET OG HOLDBARHET — det målbare grunnlaget for «varer lenger»
# ─────────────────────────────────────────────────────────────────────────────
_QUALITY = [
    dict(
        slug="slitestyrke", name="Slitestyrke og pilling", kind="kvalitet",
        blurb="Martindale-sykluser, strekkfasthet og nupper. Bestemmes av fibertype, "
              "garnvridning og stoffkonstruksjon — ikke av pris.",
        terms=["abrasion resistance", "martindale", "pilling", "tensile strength fabric",
               "tear strength", "seam slippage", "wear resistance fabric", "snagging"],
    ),
    dict(
        slug="fargeekthet", name="Fargeekthet", kind="kvalitet",
        blurb="Om fargen holder seg gjennom vask, gnissing, svette og lys. "
              "Dårlig fargeekthet betyr også at fargestoff vandrer mot huden.",
        terms=["colour fastness", "color fastness", "wash fastness", "rubbing fastness",
               "crocking", "light fastness", "perspiration fastness", "dye migration"],
    ),
    dict(
        slug="formstabilitet", name="Krymp og formstabilitet", kind="kvalitet",
        blurb="Dimensjonsendring ved vask og gjenoppretting etter strekk. Den vanligste "
              "grunnen til at et plagg legges bort før det er slitt ut.",
        terms=["dimensional stability", "shrinkage fabric", "shrinkage textile",
               "fabric relaxation", "recovery elastane", "bagging fabric", "garment fit loss"],
    ),
    dict(
        slug="vaskeholdbarhet", name="Vaskeholdbarhet og levetid", kind="kvalitet",
        blurb="Hvor mange vask og bruk et plagg faktisk tåler. Den eneste "
              "kvalitetsmålingen som direkte oversettes til kroner per bruk.",
        terms=["laundering cycles", "washing cycles fabric", "garment lifetime",
               "number of wears", "durability apparel", "product lifetime clothing",
               "premature disposal clothing"],
    ),
    dict(
        slug="komfort-og-pust", name="Komfort, pust og fukttransport", kind="kvalitet",
        blurb="Fukttransport, luftgjennomtrengelighet og termisk komfort. "
              "Det som avgjør om plagget føles godt, målt i stedet for påstått.",
        terms=["moisture management fabric", "wicking", "air permeability fabric",
               "thermal comfort clothing", "breathability fabric", "water vapour resistance"],
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# MILJØ OG LIVSLØP
# ─────────────────────────────────────────────────────────────────────────────
_ENVIRONMENT = [
    dict(
        slug="mikrofiberutslipp", name="Mikrofiberutslipp", kind="miljo",
        blurb="Fiberfragmenter som løsner ved vask og bruk. Gjelder alle fibre, men "
              "syntetene brytes ikke ned — og fragmentene inhaleres innendørs.",
        terms=["microfibre", "microfiber", "microplastic", "fibre shedding",
               "fiber shedding", "fibre release laundering", "textile microplastic"],
    ),
    dict(
        slug="farge-og-vannforbruk", name="Farging, vann og avløp", kind="miljo",
        blurb="Farging og etterbehandling er der tekstilindustriens vann- og "
              "kjemikalieforbruk faktisk ligger.",
        terms=["textile wastewater", "dyeing effluent", "water footprint textile",
               "water consumption textile", "dye degradation wastewater",
               "textile effluent treatment"],
    ),
    dict(
        slug="nedbrytbarhet", name="Biologisk nedbrytbarhet", kind="miljo",
        blurb="Hva som skjer med fiberen etter bruk. Naturfibre brytes ned — men "
              "ikke nødvendigvis når de er kjemisk behandlet.",
        terms=["biodegradation textile", "biodegradability fibre", "biodegradability fiber",
               "compostable textile", "soil burial test"],
    ),
    dict(
        slug="resirkulerbarhet", name="Resirkulerbarhet", kind="miljo",
        blurb="Fiberblandinger er hovedhindringen: en bomull/polyester-blanding kan i "
              "praksis ikke materialgjenvinnes i dag.",
        terms=["textile recycling", "fibre recycling", "fiber recycling", "circularity textile",
               "blended fabric separation", "chemical recycling textile", "post-consumer textile"],
    ),
    dict(
        slug="livslopsanalyse", name="Livsløpsanalyse", kind="miljo",
        blurb="LCA-tall for plagg. Nyttig for å se hvilket steg som faktisk dominerer — "
              "og for å avsløre påstander som ikke tåler regnestykket.",
        terms=["life cycle assessment textile", "life cycle assessment apparel",
               "carbon footprint clothing", "environmental impact apparel",
               "lca garment", "greenhouse gas apparel"],
    ),
]

TOPICS: list[dict] = _FIBERS + _TREATMENTS + _QUALITY + _ENVIRONMENT

TOPICS_BY_SLUG: dict[str, dict] = {t["slug"]: t for t in TOPICS}

# Rekkefølgen gruppene vises i, og visningsnavn. Nettsiden leser dette fra KB-en.
KIND_LABELS: dict[str, str] = {
    "fiber": "Fibre",
    "behandling": "Behandlinger og kjemikalier",
    "kvalitet": "Kvalitet og holdbarhet",
    "miljo": "Miljø og livsløp",
}
KIND_ORDER: list[str] = ["fiber", "behandling", "kvalitet", "miljo"]

# Innkjøpsdommen et emne kan ende på. Rekkefølgen er «mest inngripende først» og styrer
# sorteringen på kravsiden.
VERDICTS: dict[str, str] = {
    "unngaa": "Utelukkes",
    "dokumenter": "Krever dokumentasjon",
    "foretrekk": "Foretrekkes",
    "noeytral": "Ingen innvending",
    "ukjent": "For tynt grunnlag",
}
VERDICT_ORDER: list[str] = ["unngaa", "dokumenter", "foretrekk", "noeytral", "ukjent"]

STRENGTHS: dict[str, str] = {
    "sterk": "Sterk evidens",
    "moderat": "Moderat evidens",
    "svak": "Svak evidens",
}
