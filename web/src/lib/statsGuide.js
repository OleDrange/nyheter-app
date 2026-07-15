// Faglig innhold for «Slik leser du forskningstall» (StatsGuide.astro).
// Rent datadrevet: legg til/endre noder her — UI-et rendrer treet generisk.
//
// Nodeform (rekursiv):
//   { id, title, icon?, tagline?, keywords?, sections?: [{ label, html, formula? }], children? }
// - id må være unikt i hele treet; brukes som DOM-id og URL-hash (#les-<id>).
// - tagline vises nedtonet ved siden av tittelen (kort «når brukes dette»).
// - sections vises når noden utvides, over eventuelle barn. formula: true →
//   rendres som formelblokk (mono-font, egen bakgrunn).
// - keywords utvider søkefeltets treff utover tittel/tagline.
//
// Ny metode = nytt barn under riktig datatype; nytt regneeksempel = nytt barn
// under metoden (konvensjon: id `<metode>-eksempel`, title «Eksempel med tolkning»).

// Inline-formel med skjermleser-tekst.
const fm = (html, aria) => `<span class="sg-math" role="math" aria-label="${aria}">${html}</span>`;

export const STATS_GUIDE = [
  {
    id: 'grunnbegreper',
    icon: '🧭',
    title: 'Grunnbegreper: p-verdi & konfidensintervall',
    tagline: 'start her — nøkkelen til alt under',
    keywords: 'p-verdi konfidensintervall ki ci signifikans grunnlinje',
    sections: [
      {
        label: 'Når',
        html: 'To tall går igjen i nesten alle studier: p-verdien og konfidensintervallet. Kan du lese dem, kan du vurdere de fleste resultater selv.',
      },
    ],
    children: [
      {
        id: 'p-verdi',
        title: 'P-verdi',
        keywords: 'signifikant statistisk 0.05 nullhypotese',
        sections: [
          {
            label: 'Hva',
            html: 'Sannsynligheten for å se et resultat minst så ekstremt som det observerte <em>hvis nulleffekten var sann</em>. <strong>p &lt; 0.05</strong> regnes konvensjonelt som «statistisk signifikant».',
          },
          {
            label: 'Viktig',
            html: 'P-verdien sier <strong>ikke</strong> hvor stor eller viktig effekten er — bare hvor uventet resultatet ville vært uten en reell effekt. Størrelsen leses av effektstørrelsen, ikke p-verdien.',
          },
        ],
      },
      {
        id: 'konfidensintervall',
        title: '95 % konfidensintervall (KI)',
        keywords: 'ki ci intervall spenn usikkerhet grunnlinje krysser',
        sections: [
          {
            label: 'Hva',
            html: 'Det plausible spennet for den sanne effekten i populasjonen — punktestimatet er beste gjetning, KI viser usikkerheten rundt det.',
          },
          {
            label: 'Nøkkelregel',
            html: `Krysser KI <strong>ikke</strong> grunnlinjen → signifikant (p &lt; 0.05). Krysser det grunnlinjen → ikke signifikant (p &gt; 0.05). Grunnlinjen er <strong>0.0</strong> for standardiserte gjennomsnittsforskjeller (SMD), Cliff's delta og assosiasjonsmål — og <strong>1.0</strong> for RR/OR/HR.`,
          },
        ],
        children: [
          {
            id: 'konfidensintervall-eksempel',
            title: 'Eksempel: les et KI i praksis',
            keywords: 'eksempel tolkning',
            sections: [
              {
                label: 'Eksempel',
                html: `En studie rapporterer ${fm('RR = 0.75', 'relativ risiko er lik 0.75')}, 95&nbsp;% KI [0.60,&nbsp;0.94].`,
              },
              {
                label: 'Slik leses tallene',
                html: 'Beste gjetning er 25 % lavere risiko. Hele spennet ligger under grunnlinjen 1.0 — i verste fall 6 % lavere (0.94), i beste 40 % (0.60) — så funnet er signifikant. Hadde KI vært [0.60, 1.05], ville det krysset 1.0, og funnet ville ikke vært signifikant.',
              },
            ],
          },
        ],
      },
      {
        id: 'effekt-vs-signifikans',
        title: 'Effektstørrelse vs. signifikans',
        keywords: 'effektstørrelse utvalgsstørrelse store utvalg',
        sections: [
          {
            label: 'Hva',
            html: 'Signifikans svarer på «finnes det en effekt?» — effektstørrelsen på «hvor stor er den?». Du trenger begge.',
          },
          {
            label: 'Viktig',
            html: 'Store utvalg kan gi signifikante, men bittesmå effekter. En p-verdi på 0.001 kan høre til en effekt som er for liten til å bety noe i praksis.',
          },
        ],
      },
    ],
  },
  {
    id: 'kontinuerlig',
    icon: '📈',
    title: 'Kontinuerlige data',
    tagline: 'numeriske verdier: vekt, blodtrykk, tid',
    keywords: 'gjennomsnitt standardavvik smd mean kontinuerlig',
    sections: [
      {
        label: 'Når',
        html: 'Utfallet er en tallverdi. Studien sammenligner typisk gjennomsnitt mellom to grupper, og effekten uttrykkes i standardavvik (grunnlinje 0.0).',
      },
    ],
    children: [
      {
        id: 'cohens-d',
        title: "Cohen's d",
        keywords: 'effektstørrelse standardavvik pooled 0.2 0.5 0.8',
        sections: [
          {
            label: 'Hva',
            html: 'Standardmålet på hvor stor forskjellen mellom to gruppegjennomsnitt er, uttrykt i standardavvik.',
          },
          {
            label: 'Formel',
            formula: true,
            html: fm(
              'd = (M<sub>1</sub> − M<sub>2</sub>) / SD<sub>pooled</sub><br>SD<sub>pooled</sub> = √[((n<sub>1</sub>−1)s<sub>1</sub><sup>2</sup> + (n<sub>2</sub>−1)s<sub>2</sub><sup>2</sup>) / (n<sub>1</sub>+n<sub>2</sub>−2)]',
              'd er lik M1 minus M2 delt på samlet standardavvik, der samlet standardavvik er kvadratroten av: n1 minus 1 ganger s1 i andre, pluss n2 minus 1 ganger s2 i andre, delt på n1 pluss n2 minus 2'
            ),
          },
          {
            label: 'Hvorfor',
            html: 'Gjør forskjeller sammenlignbare på tvers av måleskalaer (mmHg, kg, sekunder). Egnet ved større utvalg (N &gt; 20 per gruppe) — ved mindre utvalg, se Hedges’ g.',
          },
          {
            label: 'Tolkning',
            html: '0.2 = liten, 0.5 = moderat, 0.8 = stor effekt. Grunnlinje 0.0.',
          },
        ],
        children: [
          {
            id: 'cohens-d-eksempel',
            title: 'Eksempel med tolkning',
            keywords: 'eksempel blodtrykk',
            sections: [
              {
                label: 'Eksempel',
                html: `Ny behandling senker blodtrykk: ${fm('M<sub>1</sub> − M<sub>2</sub> = 8 mmHg', 'M1 minus M2 er lik 8 millimeter kvikksølv')} og ${fm('SD<sub>pooled</sub> = 13', 'samlet standardavvik er lik 13')} → ${fm('d = 8 / 13 ≈ 0.62', 'd er lik 8 delt på 13, cirka 0.62')}, 95&nbsp;% KI [0.30,&nbsp;0.94].`,
              },
              {
                label: 'Slik leses tallene',
                html: 'Moderat-til-stor effekt: behandlingsgruppen ligger ca. 0.62 standardavvik bedre an. KI krysser <strong>ikke</strong> grunnlinjen 0.0 → signifikant (p &lt; 0.05), men spennet er bredt — den sanne effekten kan være alt fra liten (0.30) til stor (0.94).',
              },
            ],
          },
        ],
      },
      {
        id: 'hedges-g',
        title: "Hedges' g",
        keywords: 'små utvalg korreksjon bias',
        sections: [
          {
            label: 'Hva',
            html: "Cohen's d med en korreksjonsfaktor for små utvalg.",
          },
          {
            label: 'Formel',
            formula: true,
            html: fm(
              'g = d × J,&ensp;J ≈ 1 − 3 / (4(n<sub>1</sub>+n<sub>2</sub>) − 9)',
              'g er lik d ganger J, der J er cirka 1 minus 3 delt på 4 ganger summen n1 pluss n2, minus 9'
            ),
          },
          {
            label: 'Hvorfor',
            html: "Cohen's d overestimerer effekten ved små utvalg; korreksjonsfaktoren J justerer for det. Foretrukket ved N &lt; 20 per gruppe.",
          },
          {
            label: 'Tolkning',
            html: 'Samme benchmarks som Cohen’s d: 0.2 / 0.5 / 0.8.',
          },
        ],
        children: [
          {
            id: 'hedges-g-eksempel',
            title: 'Eksempel med tolkning',
            keywords: 'eksempel',
            sections: [
              {
                label: 'Eksempel',
                html: `Med ${fm('n<sub>1</sub> = n<sub>2</sub> = 10', 'n1 er lik n2 er lik 10')} og ${fm('d = 0.80', 'd er lik 0.80')} → ${fm('J ≈ 1 − 3/71 ≈ 0.96', 'J er cirka 1 minus 3 delt på 71, cirka 0.96')}, så ${fm('g ≈ 0.77', 'g er cirka 0.77')}.`,
              },
              {
                label: 'Slik leses tallene',
                html: 'Litt mindre enn d — det er nettopp korreksjonen for lite utvalg. Med bare 10 per gruppe er 0.77 det mer edruelige estimatet av effekten.',
              },
            ],
          },
        ],
      },
      {
        id: 'glass-delta',
        title: "Glass' delta (Δ)",
        keywords: 'kontrollgruppe spredning varians',
        sections: [
          {
            label: 'Hva',
            html: 'Gjennomsnittsforskjellen målt kun mot <strong>kontrollgruppens</strong> spredning.',
          },
          {
            label: 'Formel',
            formula: true,
            html: fm(
              'Δ = (M<sub>1</sub> − M<sub>2</sub>) / SD<sub>kontroll</sub>',
              'delta er lik M1 minus M2 delt på kontrollgruppens standardavvik'
            ),
          },
          {
            label: 'Hvorfor',
            html: 'Nyttig når behandlingen antas å endre spredningen i behandlingsgruppen — da ville en samlet SD (som i Cohen’s d) vært misvisende.',
          },
        ],
        children: [
          {
            id: 'glass-delta-eksempel',
            title: 'Eksempel med tolkning',
            keywords: 'eksempel',
            sections: [
              {
                label: 'Eksempel',
                html: `${fm('M<sub>1</sub> − M<sub>2</sub> = 6', 'M1 minus M2 er lik 6')} og ${fm('SD<sub>kontroll</sub> = 10', 'kontrollgruppens standardavvik er lik 10')} → ${fm('Δ = 0.60', 'delta er lik 0.60')}.`,
              },
              {
                label: 'Slik leses tallene',
                html: 'Moderat effekt målt mot kontrollgruppens baseline-spredning: behandlingsgruppen ligger 0.6 «kontroll-standardavvik» bedre an.',
              },
            ],
          },
        ],
      },
      {
        id: 'smd',
        title: 'Standardisert gjennomsnittsforskjell (SMD)',
        keywords: 'smd standardized mean difference metaanalyse samlebegrep standardavvik',
        sections: [
          {
            label: 'Hva',
            html: "Samlebegrepet for gjennomsnittsforskjeller uttrykt i standardavvik — Cohen's d, Hedges' g og Glass' Δ er alle SMD-varianter. Når en metaanalyse rapporterer «SMD», er det som regel Hedges' g som er brukt per studie og deretter vektet sammen.",
          },
          {
            label: 'Formel',
            formula: true,
            html: fm(
              'SMD<sub>i</sub> = (M<sub>1</sub> − M<sub>2</sub>) / SD<sub>pooled</sub>&ensp;per studie<br>SMD = Σ(w<sub>i</sub> · SMD<sub>i</sub>) / Σw<sub>i</sub>&ensp;(samlet, vektet som WMD)',
              'SMD for hver studie er lik M1 minus M2 delt på samlet standardavvik; den samlede SMD er summen av vekt i ganger SMD i, delt på summen av vektene'
            ),
          },
          {
            label: 'Hvorfor',
            html: 'Brukes i metaanalyser når studiene måler samme utfall på <strong>ulike skalaer</strong> (f.eks. tre forskjellige søvnkvalitet-spørreskjemaer) — standardavvik blir fellesvalutaen som gjør dem sammenlignbare. Måler alle på samme skala, gir WMD mer lettleste tall i originalenheter.',
          },
          {
            label: 'Tolkning',
            html: 'Samme benchmarks som Cohen’s d: 0.2 = liten, 0.5 = moderat, 0.8 = stor. Grunnlinje 0.0. Prisen for standardiseringen: «0.45 standardavvik» må oversettes tilbake (ganges med et typisk SD) for å bli en klinisk størrelse.',
          },
        ],
        children: [
          {
            id: 'smd-eksempel',
            title: 'Eksempel med tolkning',
            keywords: 'eksempel søvn metaanalyse',
            sections: [
              {
                label: 'Eksempel',
                html: `En metaanalyse av 6 studier med ulike søvnkvalitet-skalaer finner ${fm('SMD = 0.45', 'SMD er lik 0.45')}, 95&nbsp;% KI [0.21,&nbsp;0.69].`,
              },
              {
                label: 'Slik leses tallene',
                html: 'Moderat effekt: behandlingsgruppene ligger samlet ca. 0.45 standardavvik bedre an, uansett hvilken skala den enkelte studien brukte. KI krysser ikke grunnlinjen 0.0 → signifikant (p &lt; 0.05). For å konkretisere: bruker skalaen din SD ≈ 2 poeng, tilsvarer effekten ca. 0.9 poeng.',
              },
            ],
          },
        ],
      },
      {
        id: 'wmd',
        title: 'Vektet gjennomsnittsforskjell (WMD)',
        keywords: 'wmd md mean difference metaanalyse vektet gjennomsnitt originalenheter',
        sections: [
          {
            label: 'Hva',
            html: 'Metaanalysens gjennomsnittsforskjell i <strong>originalenheter</strong> (mmHg, kg, minutter): hver studies forskjell vektes sammen til ett samlet estimat. Kalles også bare MD (mean difference).',
          },
          {
            label: 'Formel',
            formula: true,
            html: fm(
              'WMD = Σ(w<sub>i</sub> · MD<sub>i</sub>) / Σw<sub>i</sub>,&ensp;w<sub>i</sub> = 1 / SE<sub>i</sub><sup>2</sup>',
              'WMD er lik summen av vekt i ganger gjennomsnittsforskjell i, delt på summen av vektene, der vekten for hver studie er 1 delt på studiens standardfeil i andre'
            ),
          },
          {
            label: 'Hvorfor',
            html: 'Brukes i metaanalyser når alle studiene måler utfallet på <strong>samme skala</strong> — da kan resultatet stå i enheter leseren kjenner, i stedet for standardavvik. Store/presise studier (liten standardfeil) teller mest. Måler studiene på ulike skalaer, må man i stedet standardisere med SMD (Cohen’s d / Hedges’ g).',
          },
          {
            label: 'Tolkning',
            html: 'Leses direkte i måleenheten: WMD = −4 mmHg betyr 4 mmHg lavere i behandlingsgruppen. Grunnlinje 0.0 — krysser KI null, er funnet ikke signifikant.',
          },
        ],
        children: [
          {
            id: 'wmd-eksempel',
            title: 'Eksempel med tolkning',
            keywords: 'eksempel blodtrykk metaanalyse',
            sections: [
              {
                label: 'Eksempel',
                html: `En metaanalyse av 8 studier på samme blodtrykksintervensjon finner ${fm('WMD = −4.2 mmHg', 'WMD er lik minus 4.2 millimeter kvikksølv')}, 95&nbsp;% KI [−6.1,&nbsp;−2.3].`,
              },
              {
                label: 'Slik leses tallene',
                html: 'Samlet over studiene senker intervensjonen systolisk blodtrykk med 4.2 mmHg; den sanne effekten ligger trolig mellom 2.3 og 6.1 mmHg reduksjon. Hele KI er under grunnlinjen 0.0 → signifikant (p &lt; 0.05). Fordelen med WMD: du kan vurdere direkte om 4 mmHg er klinisk relevant — uten omvei via standardavvik.',
              },
            ],
          },
        ],
      },
    ],
  },
  {
    id: 'binaer',
    icon: '🎚️',
    title: 'Binære hendelser & telling',
    tagline: 'to utfall i separate grupper: syk/frisk, død/levende',
    keywords: 'binær risiko odds hazard 2x2 tabell',
    sections: [
      {
        label: 'Når',
        html: `Utfallet er ett av to (hendelse/ikke hendelse), talt opp i en 2×2-tabell: <em>a</em> = eksponerte med utfall, <em>b</em> = eksponerte uten, <em>c</em>/<em>d</em> = tilsvarende for ueksponerte. Grunnlinjen for alle tre målene er <strong>1.0</strong>.`,
      },
    ],
    children: [
      {
        id: 'relativ-risiko',
        title: 'Relativ risiko (RR)',
        keywords: 'rr risk ratio kohort prospektiv',
        sections: [
          {
            label: 'Hva',
            html: 'Risikoen i den eksponerte gruppen delt på risikoen i den ueksponerte.',
          },
          {
            label: 'Formel',
            formula: true,
            html: fm(
              'RR = [a/(a+b)] / [c/(c+d)]',
              'RR er lik a delt på summen a pluss b, delt på c delt på summen c pluss d'
            ),
          },
          {
            label: 'Hvorfor',
            html: 'For <strong>prospektive studier/kohortstudier</strong> der man følger grupper over tid og kan måle faktisk risiko. Intuitiv å lese: «X ganger så stor risiko».',
          },
          {
            label: 'Tolkning',
            html: '&lt; 1.0 = redusert risiko (beskyttende), &gt; 1.0 = økt risiko. Grunnlinje 1.0.',
          },
        ],
        children: [
          {
            id: 'relativ-risiko-eksempel',
            title: 'Eksempel med tolkning',
            keywords: 'eksempel',
            sections: [
              {
                label: 'Eksempel',
                html: `${fm('RR = 0.75', 'RR er lik 0.75')}, 95&nbsp;% KI [0.60,&nbsp;0.94].`,
              },
              {
                label: 'Slik leses tallene',
                html: '25 % lavere risiko i behandlingsgruppen. KI krysser ikke grunnlinjen 1.0 → signifikant (p &lt; 0.05); den sanne reduksjonen ligger trolig mellom 6 % og 40 %.',
              },
            ],
          },
        ],
      },
      {
        id: 'oddsratio',
        title: 'Oddsratio (OR)',
        keywords: 'or odds case-control retrospektiv logistisk regresjon',
        sections: [
          {
            label: 'Hva',
            html: 'Forholdet mellom oddsene for utfallet i to grupper.',
          },
          {
            label: 'Formel',
            formula: true,
            html: fm('OR = (a/b) / (c/d) = ad / bc', 'OR er lik a delt på b, delt på c delt på d — som er lik a d delt på b c'),
          },
          {
            label: 'Hvorfor',
            html: 'For <strong>retrospektive studier/case-control</strong> der ekte risiko ikke kan beregnes fordi utvalget er plukket ut fra utfallet. Også standardmålet i logistisk regresjon.',
          },
          {
            label: 'Tolkning',
            html: '&lt; 1.0 = lavere odds, &gt; 1.0 = høyere odds. Obs: OR overdriver RR når utfallet er vanlig (&gt; ~10 %).',
          },
        ],
        children: [
          {
            id: 'oddsratio-eksempel',
            title: 'Eksempel med tolkning',
            keywords: 'eksempel',
            sections: [
              {
                label: 'Eksempel',
                html: `${fm('OR = 2.1', 'OR er lik 2.1')}, 95&nbsp;% KI [1.4,&nbsp;3.2].`,
              },
              {
                label: 'Slik leses tallene',
                html: 'Eksponerte har omtrent doblet odds for utfallet. Hele KI ligger over 1.0 → signifikant.',
              },
            ],
          },
        ],
      },
      {
        id: 'hazardratio',
        title: 'Hazardratio (HR)',
        keywords: 'hr overlevelse cox kaplan-meier tid-til-hendelse survival',
        sections: [
          {
            label: 'Hva',
            html: 'Forholdet mellom hendelses<em>rater</em> over tid i to grupper.',
          },
          {
            label: 'Formel',
            formula: true,
            html: fm(
              'HR = hazard(behandling) / hazard(kontroll)',
              'HR er lik hasardraten i behandlingsgruppen delt på hasardraten i kontrollgruppen'
            ),
          },
          {
            label: 'Hvorfor',
            html: 'For <strong>tid-til-hendelse-/overlevelsesanalyse</strong> (Kaplan–Meier, Cox-regresjon). Tar hensyn til <em>når</em> hendelsene skjer, og til deltakere som faller ut underveis (sensurerte observasjoner).',
          },
          {
            label: 'Tolkning',
            html: '&lt; 1.0 = langsommere/færre hendelser (beskyttende), &gt; 1.0 = raskere/flere hendelser.',
          },
        ],
        children: [
          {
            id: 'hazardratio-eksempel',
            title: 'Eksempel med tolkning',
            keywords: 'eksempel',
            sections: [
              {
                label: 'Eksempel',
                html: `${fm('HR = 0.80', 'HR er lik 0.80')}, 95&nbsp;% KI [0.68,&nbsp;0.95].`,
              },
              {
                label: 'Slik leses tallene',
                html: '20 % lavere hazard (hendelsesrate) i behandlingsgruppen gjennom oppfølgingstiden. KI krysser ikke 1.0 → signifikant.',
              },
            ],
          },
        ],
      },
    ],
  },
  {
    id: 'ordinal',
    icon: '📊',
    title: 'Ordinal- & rangeringsdata',
    tagline: 'ordnede kategorier uten eksakt avstand: smerteskår, spørreskjema',
    keywords: 'ordinal rang ikke-parametrisk likert skala',
    sections: [
      {
        label: 'Når',
        html: 'Kategoriene har en rekkefølge, men «avstanden» mellom trinnene er ukjent (smerte 3 → 4 ≠ 7 → 8). Derfor brukes ikke-parametriske metoder basert på ranger. Grunnlinje for effektstørrelsen: <strong>0.0</strong>.',
      },
    ],
    children: [
      {
        id: 'mann-whitney',
        title: 'Mann–Whitney U-test',
        keywords: 'u-test uavhengige grupper wilcoxon rank sum',
        sections: [
          {
            label: 'Hva',
            html: 'Tester om én av <strong>to uavhengige grupper</strong> systematisk skårer høyere enn den andre.',
          },
          {
            label: 'Idé',
            html: 'Ranger alle observasjonene samlet (begge grupper under ett), og summer rangene per gruppe. Skjev rangsum → systematisk forskjell.',
          },
          {
            label: 'Hvorfor',
            html: 'For rangerte eller ikke-normalfordelte data der t-testens forutsetninger ikke holder.',
          },
        ],
      },
      {
        id: 'wilcoxon',
        title: 'Wilcoxon signed-rank-test',
        keywords: 'parede matchede før etter',
        sections: [
          {
            label: 'Hva',
            html: 'Motstykket til Mann–Whitney for <strong>parede/matchede</strong> målinger.',
          },
          {
            label: 'Hvorfor',
            html: 'Når samme person måles to ganger (f.eks. før/etter en intervensjon) med ordinaldata — parene er ikke uavhengige, så U-testen kan ikke brukes.',
          },
        ],
      },
      {
        id: 'cliffs-delta',
        title: "Cliff's delta (d)",
        keywords: 'effektstørrelse dominans par sannsynlighet',
        sections: [
          {
            label: 'Hva',
            html: 'Effektstørrelsen for rangdata: andelen par der A skårer høyere enn B, minus andelen der A skårer lavere.',
          },
          {
            label: 'Formel',
            formula: true,
            html: fm(
              'd = [#(x &gt; y) − #(x &lt; y)] / (n<sub>1</sub> · n<sub>2</sub>)',
              'd er lik antall par der x er større enn y, minus antall par der x er mindre enn y, delt på n1 ganger n2'
            ),
          },
          {
            label: 'Hvorfor',
            html: 'Uttrykker sannsynligheten for at en tilfeldig person fra gruppe A skårer høyere enn en tilfeldig fra gruppe B. Robust for ordinaldata — krever ingen antakelse om avstand mellom trinnene.',
          },
          {
            label: 'Tolkning',
            html: 'Spenner fra −1 til +1; 0 = ingen forskjell. Vanlige terskler for |d|: ~0.11 ubetydelig, 0.15 liten, 0.33 middels, 0.47 stor. Grunnlinje 0.0.',
          },
        ],
        children: [
          {
            id: 'cliffs-delta-eksempel',
            title: 'Eksempel med tolkning',
            keywords: 'eksempel',
            sections: [
              {
                label: 'Eksempel',
                html: `${fm('d = 0.28', 'd er lik 0.28')}.`,
              },
              {
                label: 'Slik leses tallene',
                html: 'Liten-til-middels effekt (nær 0.33-terskelen): gruppe A skårer høyere enn B i klart flere av parene enn omvendt — i 28 prosentpoeng flere av sammenligningene.',
              },
            ],
          },
        ],
      },
    ],
  },
  {
    id: 'nominal',
    icon: '🏷️',
    title: 'Nominale kategorier',
    tagline: 'etiketter uten rekkefølge: blodtype, idrettsgren',
    keywords: 'nominal kategori assosiasjon frekvens krysstabell',
    sections: [
      {
        label: 'Når',
        html: 'Kategoriene har ingen naturlig rekkefølge. Spørsmålet er om fordelingen/assosiasjonen mellom kategoriene avviker fra tilfeldighet. Effektstørrelsene går fra <strong>0</strong> (ingen assosiasjon) til <strong>1</strong> (perfekt).',
      },
    ],
    children: [
      {
        id: 'kjikvadrat',
        title: 'Kjikvadrattest (χ²)',
        keywords: 'chi square kji observert forventet uavhengighet',
        sections: [
          {
            label: 'Hva',
            html: 'Tester om fordelingen over kategorier avviker fra det man ville forventet ved ren tilfeldighet/uavhengighet.',
          },
          {
            label: 'Formel',
            formula: true,
            html: fm(
              'χ<sup>2</sup> = Σ (O − E)<sup>2</sup> / E',
              'kji i andre er lik summen av: observert minus forventet frekvens i andre, delt på forventet frekvens'
            ),
          },
          {
            label: 'Hvorfor',
            html: 'Standardtesten for krysstabeller: O = observert frekvens, E = forventet frekvens hvis variablene var uavhengige. Gir en p-verdi, men ingen effektstørrelse — se Cramér’s V og phi.',
          },
        ],
      },
      {
        id: 'cramers-v',
        title: "Cramér's V",
        keywords: 'effektstørrelse assosiasjon tabell',
        sections: [
          {
            label: 'Hva',
            html: 'χ² omregnet til en effektstørrelse mellom 0 og 1.',
          },
          {
            label: 'Formel',
            formula: true,
            html: fm(
              'V = √[ χ<sup>2</sup> / (N · (k − 1)) ],&ensp;k = min(rader, kolonner)',
              'V er lik kvadratroten av kji i andre delt på N ganger k minus 1, der k er det minste av antall rader og kolonner'
            ),
          },
          {
            label: 'Hvorfor',
            html: 'χ² alene vokser med utvalgsstørrelsen og sier ikke hvor sterk sammenhengen er. V normaliserer den til 0–1 og fungerer også for tabeller større enn 2×2.',
          },
          {
            label: 'Tolkning',
            html: '0 = ingen assosiasjon, 1 = perfekt. Grovt: ~0.1 svak, ~0.3 moderat, ~0.5 sterk.',
          },
        ],
        children: [
          {
            id: 'cramers-v-eksempel',
            title: 'Eksempel med tolkning',
            keywords: 'eksempel',
            sections: [
              {
                label: 'Eksempel',
                html: `${fm('V = 0.18', 'V er lik 0.18')}.`,
              },
              {
                label: 'Slik leses tallene',
                html: 'En svak, men reell sammenheng mellom kategori og utfall — verdt å merke seg, men kategorien forklarer bare en liten del av variasjonen.',
              },
            ],
          },
        ],
      },
      {
        id: 'phi',
        title: 'Phi (φ)',
        keywords: 'to ganger to binære variabler korrelasjon',
        sections: [
          {
            label: 'Hva',
            html: 'Korrelasjonslignende effektstørrelse — spesialtilfellet av Cramér’s V for <strong>2×2-tabeller</strong>.',
          },
          {
            label: 'Formel',
            formula: true,
            html: fm('φ = √( χ<sup>2</sup> / N )', 'phi er lik kvadratroten av kji i andre delt på N'),
          },
          {
            label: 'Hvorfor',
            html: 'Enkelt mål når begge variablene er binære; tolkes omtrent som en korrelasjonskoeffisient.',
          },
        ],
      },
    ],
  },
];
